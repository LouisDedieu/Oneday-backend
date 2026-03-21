"""
Service Supabase pour la gestion de la base de données
"""
import logging
import asyncio
import httpx
from typing import Optional, Dict, List
from datetime import datetime

logger = logging.getLogger("bombo.supabase_service")


class SupabaseService:
    """Service de gestion des interactions avec Supabase"""

    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = url
        self.key = key
        self.supabase_client = None

        if url and key:
            self._init_clients()
            self._check_service_role_key()

    def _init_clients(self):
        """Initialise les clients Supabase"""
        try:
            from supabase import create_client

            self.supabase_client = create_client(self.url, self.key)
            logger.info("Supabase SDK initialisé ✓")
        except ImportError:
            logger.warning(
                "Module supabase-py non installé. Fonctionnement en mode mémoire."
            )

    def _check_service_role_key(self):
        """Vérifie que la clé utilisée est une clé service_role"""
        if self.key.startswith("sb_secret_"):
            logger.info("SUPABASE_SERVICE_ROLE_KEY = service_role ✓  (RLS bypassed)")
        elif self.key.startswith("sb_publishable_"):
            logger.warning(
                "⚠️  SUPABASE_SERVICE_ROLE_KEY est la clé anon (sb_publishable_) — attendu sb_secret_. "
                "Les insertions seront bloquées par RLS. "
                "→ Supabase dashboard → Settings → API → copiez la clé 'service_role'."
            )
        else:
            logger.info("SUPABASE_SERVICE_ROLE_KEY configurée ✓")

    def is_configured(self) -> bool:
        """Vérifie si Supabase est configuré"""
        return self.url is not None and self.key is not None

    def _get_headers(self) -> Dict[str, str]:
        """Retourne les headers pour les requêtes directes à l'API"""
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _get_url(self, table: str) -> str:
        """Construit l'URL pour une table donnée"""
        return f"{self.url}/rest/v1/{table}"

    async def insert(self, table: str, payload: Dict) -> Dict:
        """
        Insère une ligne via l'API PostgREST avec la clé service.
        Retourne la ligne insérée.
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self._get_url(table),
                json=payload,
                headers=self._get_headers(),
                timeout=10,
            )
            response.raise_for_status()
            rows = response.json()
            return rows[0] if rows else {}

    async def update(
            self, table: str, payload: Dict, eq_col: str, eq_val: str
    ) -> None:
        """Met à jour des lignes via l'API PostgREST"""
        params = {eq_col: f"eq.{eq_val}"}
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                self._get_url(table),
                json=payload,
                params=params,
                headers=self._get_headers(),
                timeout=10,
            )
            response.raise_for_status()

    async def create_job(
            self, job_id: str, url: str, user_id: Optional[str] = None
    ) -> None:
        """Crée un job dans analysis_jobs"""
        if not self.is_configured():
            return
        try:
            await self.insert(
                "analysis_jobs",
                {
                    "id": job_id,
                    "source_url": url,
                    "user_id": user_id,
                    "status": "pending",
                },
            )
            logger.info(f"Job {job_id} créé dans Supabase ✓")
        except Exception as e:
            logger.error(f"Erreur création job Supabase: {e}")

    async def update_job(self, job_id: str, updates: Dict):
        """Met à jour un job dans la table analysis_jobs"""
        if not self.is_configured():
            return
        try:
            await self.update("analysis_jobs", updates, "id", job_id)
            logger.debug(f"Job {job_id} mis à jour dans Supabase")
        except Exception as e:
            logger.error(f"Erreur mise à jour Supabase pour job {job_id}: {e}")

    async def create_trip(
            self, trip_data: Dict, job_id: str, user_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Crée un voyage complet dans Supabase avec toutes ses relations.
        Retourne l'ID du trip créé.
        """
        if not self.is_configured():
            return None

        def _do_insert() -> Optional[str]:
            """Insertion synchrone dans un thread séparé"""
            import httpx as _httpx

            def _sb_insert(table: str, payload: dict) -> dict:
                r = _httpx.post(
                    self._get_url(table),
                    json=payload,
                    headers=self._get_headers(),
                    timeout=10,
                )
                if not r.is_success:
                    logger.error("❌ %s → %s | body: %s", table, r.status_code, r.text)
                r.raise_for_status()
                rows = r.json()
                return rows[0] if rows else {}

            # 1. Trip principal
            trip_insert = {
                "job_id": job_id,
                "user_id": user_id,
                "trip_title": trip_data.get("trip_title"),
                "vibe": trip_data.get("vibe"),
                # duration_days n'est pas renseigné ici — le trigger Postgres
                # trg_update_trip_duration le calcule automatiquement
                # à chaque INSERT/DELETE sur itinerary_days
                "best_season": trip_data.get("best_season"),
                "source_url": trip_data.get("source_url"),
                "normalized_source_url": trip_data.get("normalized_source_url"),
                "content_creator_handle": trip_data.get("content_creator", {}).get(
                    "handle"
                ),
                "content_creator_links": trip_data.get("content_creator", {}).get(
                    "links_mentioned", []
                ),
            }

            try:
                trip_row = _sb_insert("trips", trip_insert)
            except _httpx.HTTPStatusError as e:
                body = e.response.text
                if "22P02" in body or "season" in body.lower() or "enum" in body.lower():
                    logger.warning(
                        "Enum season_type incompatible (%r) → retry sans best_season. "
                        "Corps de l'erreur : %s",
                        trip_insert.get("best_season"),
                        body[:200],
                    )
                    trip_insert["best_season"] = None
                    trip_row = _sb_insert("trips", trip_insert)
                else:
                    raise

            trip_id = trip_row["id"]
            logger.info(f"Trip créé dans Supabase: {trip_id}")

            # 2. Destinations — on conserve un mapping city (lower) → destination_id
            #    pour pouvoir relier les itinerary_days à leur destination sans matching flou côté front
            city_to_dest_id: dict[str, str] = {}
            for dest in trip_data.get("destinations", []):
                dest_row = _sb_insert(
                    "destinations",
                    {
                        "trip_id": trip_id,
                        "city": dest.get("city"),
                        "country": dest.get("country"),
                        "days_spent": dest.get("days_spent"),
                        "visit_order": dest.get("order", 0),
                    },
                )
                if dest_row.get("id") and dest.get("city"):
                    city_to_dest_id[dest["city"].lower().strip()] = dest_row["id"]

            logger.info(f"city_to_dest_id: {city_to_dest_id}")

            # 3. Itinéraire
            for day_data in trip_data.get("itinerary", []):
                location = day_data.get("location")
                destination_id = city_to_dest_id.get(location.lower().strip()) if location else None

                day_row_data: dict = {
                    "trip_id": trip_id,
                    "day_number": day_data.get("day"),
                    "location": location,
                    "theme": day_data.get("theme"),
                    "destination_id": destination_id,  # FK vers destinations — renseignée dès l'insertion
                    "validated": True,                  # tous les jours démarrent validés, l'user choisit en ReviewMode
                }

                acc = day_data.get("accommodation") or {}
                if acc:
                    day_row_data.update(
                        {
                            "accommodation_name": acc.get("name"),
                            "accommodation_type": acc.get("type"),
                            "accommodation_price_per_night": acc.get("price_per_night"),
                            "accommodation_tips": acc.get("tips"),
                        }
                    )

                meals = day_data.get("meals") or {}
                if meals:
                    day_row_data.update(
                        {
                            "breakfast_spot": meals.get("breakfast"),
                            "lunch_spot": meals.get("lunch"),
                            "dinner_spot": meals.get("dinner"),
                        }
                    )

                day_row = _sb_insert("itinerary_days", day_row_data)
                day_id = day_row["id"]

                # 4. Spots
                # Mapping des types ML vers les valeurs d'enum Supabase
                TYPE_TO_SPOT_TYPE = {
                    "food": "restaurant",
                    "restaurant": "restaurant",
                    "culture": "attraction",
                    "museum": "museum",
                    "nature": "attraction",
                    "shopping": "shopping",
                    "nightlife": "bar",
                    "bar": "bar",
                    "attraction": "attraction",
                    "accommodation": "accommodation",
                }
                for idx, spot in enumerate(day_data.get("spots", [])):
                    raw_type = spot.get("type", "attraction")
                    mapped_type = TYPE_TO_SPOT_TYPE.get(raw_type, "attraction")
                    _sb_insert(
                        "spots",
                        {
                            "itinerary_day_id": day_id,
                            "name": spot.get("name"),
                            "spot_type": mapped_type,
                            "address": spot.get("address"),
                            "duration_minutes": spot.get("duration_minutes"),
                            "price_range": spot.get("price_range"),
                            "price_detail": spot.get("price_detail"),
                            "tips": spot.get("tips"),
                            "highlight": spot.get("highlight", False),
                            "spot_order": idx,
                        },
                    )

            # 5. Logistique
            for idx, log in enumerate(trip_data.get("logistics", [])):
                _sb_insert(
                    "logistics",
                    {
                        "trip_id": trip_id,
                        "from_location": log.get("from"),
                        "to_location": log.get("to"),
                        "transport_mode": log.get("mode"),
                        "duration": log.get("duration"),
                        "cost": log.get("cost"),
                        "tips": log.get("tips"),
                        "travel_order": idx,
                    },
                )

            # 6. Budget
            budget = trip_data.get("budget") or {}
            if budget:
                per_day = budget.get("per_day") or {}
                breakdown = budget.get("breakdown") or {}
                _sb_insert(
                    "budgets",
                    {
                        "trip_id": trip_id,
                        "total_estimated": budget.get("total_estimated"),
                        "currency": budget.get("currency", "EUR"),
                        "per_day_min": per_day.get("min"),
                        "per_day_max": per_day.get("max"),
                        "accommodation_cost": breakdown.get("accommodation"),
                        "food_cost": breakdown.get("food"),
                        "transport_cost": breakdown.get("transport"),
                        "activities_cost": breakdown.get("activities"),
                        "money_saving_tips": budget.get("money_saving_tips", []),
                    },
                )

            # 7. Infos pratiques
            practical = trip_data.get("practical_info") or {}
            if practical:
                _sb_insert(
                    "practical_info",
                    {
                        "trip_id": trip_id,
                        "visa_required": practical.get("visa_required"),
                        "local_currency": practical.get("local_currency"),
                        "language": practical.get("language"),
                        "best_apps": practical.get("best_apps", []),
                        "what_to_pack": practical.get("what_to_pack", []),
                        "safety_tips": practical.get("safety_tips", []),
                        "things_to_avoid": practical.get("avoid", []),
                    },
                )

            logger.info(f"Trip {trip_id} complètement créé dans Supabase ✓")
            return trip_id

        try:
            return await asyncio.to_thread(_do_insert)
        except Exception as e:
            logger.error(f"Erreur création trip dans Supabase: {e}")
            return None

    async def get_trip(self, trip_id: str) -> Optional[Dict]:
        """
        Récupère un trip par son ID avec toutes ses relations imbriquées.
        Synchronise automatiquement les spots avec les highlights des villes liées.
        """
        if not self.supabase_client:
            return None
        try:
            # 1. Récupérer le trip avec toutes ses relations
            response = (
                self.supabase_client.from_("trips")
                .select(
                    "*, destinations(*), "
                    "itinerary_days(*, spots(*)), "
                    "logistics(*), budgets(*), practical_info(*)"
                )
                .eq("id", trip_id)
                .maybe_single()
                .execute()
            )
            trip_data = response.data
            if not trip_data:
                return None

            # 2. Identifier les jours liés à des villes sauvegardées
            days_with_city = [
                day for day in trip_data.get("itinerary_days", [])
                if day.get("linked_city_id")
            ]

            if not days_with_city:
                # Pas de sync nécessaire, retourner tel quel
                return trip_data

            # 3. Récupérer les city_ids uniques
            city_ids = list(set(day["linked_city_id"] for day in days_with_city))

            # 4. Récupérer tous les highlights validés de ces villes
            highlights_res = (
                self.supabase_client.from_("city_highlights")
                .select("*")
                .in_("city_id", city_ids)
                .eq("validated", True)
                .order("highlight_order")
                .execute()
            )
            all_highlights = highlights_res.data or []

            # Grouper les highlights par city_id
            highlights_by_city: Dict[str, list] = {}
            for h in all_highlights:
                cid = h["city_id"]
                if cid not in highlights_by_city:
                    highlights_by_city[cid] = []
                highlights_by_city[cid].append(h)

            # Mapping catégorie → spot_type
            CATEGORY_TO_SPOT_TYPE = {
                "food": "restaurant",
                "culture": "attraction",
                "nature": "attraction",
                "shopping": "shopping",
                "nightlife": "bar",
                "other": "attraction",
            }

            # 5. Synchroniser chaque jour lié
            for day in days_with_city:
                city_id = day["linked_city_id"]
                day_id = day["id"]
                city_highlights = highlights_by_city.get(city_id, [])
                existing_spots = day.get("spots", [])

                # Map des spots existants par city_highlight_id
                spots_by_highlight_id = {
                    s["city_highlight_id"]: s
                    for s in existing_spots
                    if s.get("city_highlight_id")
                }

                # Set des highlight_ids actuels de la ville
                current_highlight_ids = {h["id"] for h in city_highlights}

                # Set des highlight_ids déjà liés dans les spots
                linked_highlight_ids = set(spots_by_highlight_id.keys())

                # a) Highlights à ajouter (nouveaux)
                to_add = [h for h in city_highlights if h["id"] not in linked_highlight_ids]

                # b) Spots à supprimer (highlight supprimé de la ville)
                to_delete_ids = [
                    s["id"] for s in existing_spots
                    if s.get("city_highlight_id") and s["city_highlight_id"] not in current_highlight_ids
                ]

                # c) Spots à mettre à jour (highlight modifié)
                to_update = [
                    (spots_by_highlight_id[h["id"]], h)
                    for h in city_highlights
                    if h["id"] in linked_highlight_ids
                ]

                # Exécuter les suppressions
                if to_delete_ids:
                    self.supabase_client.from_("spots") \
                        .delete() \
                        .in_("id", to_delete_ids) \
                        .execute()

                # Exécuter les mises à jour
                for spot, highlight in to_update:
                    category = highlight.get("category", "other")
                    spot_type = CATEGORY_TO_SPOT_TYPE.get(category, "attraction")
                    self.supabase_client.from_("spots") \
                        .update({
                            "name": highlight["name"],
                            "address": highlight.get("address"),
                            "tips": highlight.get("tips"),
                            "price_range": highlight.get("price_range"),
                            "latitude": highlight.get("latitude"),
                            "longitude": highlight.get("longitude"),
                            "highlight": highlight.get("is_must_see", False),
                            "spot_type": spot_type,
                        }) \
                        .eq("id", spot["id"]) \
                        .execute()

                # Calculer le prochain spot_order
                max_order = max((s.get("spot_order", 0) for s in existing_spots), default=0)

                # Exécuter les insertions
                if to_add:
                    spots_to_insert = []
                    for idx, h in enumerate(to_add):
                        category = h.get("category", "other")
                        spot_type = CATEGORY_TO_SPOT_TYPE.get(category, "attraction")
                        spots_to_insert.append({
                            "itinerary_day_id": day_id,
                            "name": h["name"],
                            "spot_type": spot_type,
                            "address": h.get("address"),
                            "duration_minutes": 60,
                            "price_range": h.get("price_range"),
                            "tips": h.get("tips"),
                            "highlight": h.get("is_must_see", False),
                            "spot_order": max_order + idx + 1,
                            "latitude": h.get("latitude"),
                            "longitude": h.get("longitude"),
                            "city_highlight_id": h["id"],
                            "source_city_id": city_id,
                        })
                    self.supabase_client.from_("spots").insert(spots_to_insert).execute()

            # 6. Re-fetch le trip avec les données synchronisées
            response = (
                self.supabase_client.from_("trips")
                .select(
                    "*, destinations(*), "
                    "itinerary_days(*, spots(*)), "
                    "logistics(*), budgets(*), practical_info(*)"
                )
                .eq("id", trip_id)
                .maybe_single()
                .execute()
            )
            trip_data = response.data

            # 7. Marquer les spots synchronisés pour le frontend
            if trip_data:
                for day in trip_data.get("itinerary_days", []):
                    for spot in day.get("spots", []):
                        if spot.get("city_highlight_id"):
                            spot["_synced_from_highlight"] = True

            return trip_data
        except Exception as e:
            logger.error(f"Erreur récupération trip {trip_id}: {e}")
            return None

    async def get_user_trips(self, user_id: str) -> List[Dict]:
        """Récupère tous les trips d'un utilisateur"""
        if not self.supabase_client:
            return []
        try:
            response = (
                self.supabase_client.from_("trip_details")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"Erreur récupération trips user {user_id}: {e}")
            return []

    # =========================================================================
    # CITIES
    # =========================================================================

    async def create_city(
            self, city_data: Dict, job_id: str, user_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Crée une city complète dans Supabase avec toutes ses relations.
        Retourne l'ID de la city créée.
        """
        if not self.is_configured():
            return None

        def _do_insert() -> Optional[str]:
            """Insertion synchrone dans un thread séparé"""
            import httpx as _httpx

            def _sb_insert(table: str, payload: dict) -> dict:
                r = _httpx.post(
                    self._get_url(table),
                    json=payload,
                    headers=self._get_headers(),
                    timeout=10,
                )
                if not r.is_success:
                    logger.error("❌ %s → %s | body: %s", table, r.status_code, r.text)
                r.raise_for_status()
                rows = r.json()
                return rows[0] if rows else {}

            # 1. City principal
            city_insert = {
                "job_id": job_id,
                "user_id": user_id,
                "city_title": city_data.get("city_title"),
                "city_name": city_data.get("city_name"),
                "country": city_data.get("country"),
                "vibe_tags": city_data.get("vibe_tags", []),
                "best_season": city_data.get("best_season"),
                "source_url": city_data.get("source_url"),
                "normalized_source_url": city_data.get("normalized_source_url"),
                "content_creator_handle": city_data.get("content_creator", {}).get("handle"),
                "content_creator_links": city_data.get("content_creator", {}).get("links_mentioned", []),
            }

            try:
                city_row = _sb_insert("cities", city_insert)
            except _httpx.HTTPStatusError as e:
                body = e.response.text
                logger.error(f"Erreur création city: {body[:200]}")
                raise

            city_id = city_row["id"]
            logger.info(f"City créée dans Supabase: {city_id}")

            # 2. Highlights
            for idx, highlight in enumerate(city_data.get("highlights", [])):
                _sb_insert(
                    "city_highlights",
                    {
                        "city_id": city_id,
                        "name": highlight.get("name"),
                        "category": highlight.get("category", "other"),
                        "subtype": highlight.get("subtype"),
                        "address": highlight.get("address"),
                        "description": highlight.get("description"),
                        "price_range": highlight.get("price_range"),
                        "tips": highlight.get("tips"),
                        "is_must_see": highlight.get("is_must_see", False),
                        "highlight_order": idx,
                        "validated": True,
                    },
                )

            # 3. Budget
            budget = city_data.get("budget") or {}
            if budget:
                _sb_insert(
                    "city_budgets",
                    {
                        "city_id": city_id,
                        "currency": budget.get("currency", "EUR"),
                        "daily_average": budget.get("daily_average"),
                        "food_average": budget.get("food_average"),
                        "transport_average": budget.get("transport_average"),
                        "activities_average": budget.get("activities_average"),
                        "accommodation_range": budget.get("accommodation_range"),
                    },
                )

            # 4. Infos pratiques
            practical = city_data.get("practical_info") or {}
            if practical:
                _sb_insert(
                    "city_practical_info",
                    {
                        "city_id": city_id,
                        "visa_required": practical.get("visa_required"),
                        "local_currency": practical.get("local_currency"),
                        "language": practical.get("language"),
                        "best_apps": practical.get("best_apps", []),
                        "what_to_pack": practical.get("what_to_pack", []),
                        "safety_tips": practical.get("safety_tips", []),
                        "things_to_avoid": practical.get("avoid", []),
                    },
                )

            logger.info(f"City {city_id} complètement créée dans Supabase ✓")
            return city_id

        try:
            return await asyncio.to_thread(_do_insert)
        except Exception as e:
            logger.error(f"Erreur création city dans Supabase: {e}")
            return None

    async def get_city(self, city_id: str) -> Optional[Dict]:
        """Récupère une city par son ID avec toutes ses relations imbriquées"""
        if not self.supabase_client:
            return None
        try:
            response = (
                self.supabase_client.from_("cities")
                .select("*, city_highlights(*), city_budgets(*), city_practical_info(*)")
                .eq("id", city_id)
                .maybe_single()
                .execute()
            )
            return response.data
        except Exception as e:
            logger.error(f"Erreur récupération city {city_id}: {e}")
            return None

    async def get_user_cities(self, user_id: str) -> List[Dict]:
        """Récupère toutes les cities d'un utilisateur"""
        if not self.supabase_client:
            return []
        try:
            response = (
                self.supabase_client.from_("city_details")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"Erreur récupération cities user {user_id}: {e}")
            return []

    # =========================================================================
    # DEDUPLICATION
    # =========================================================================

    async def find_trip_by_source_url(self, normalized_url: str) -> Optional[Dict]:
        """Cherche un trip existant par normalized_source_url. Retourne {id, trip_title, type} ou None."""
        if not self.is_configured():
            return None
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self._get_url("trips"),
                    params={
                        "normalized_source_url": f"eq.{normalized_url}",
                        "limit": "1",
                        "select": "id,trip_title",
                    },
                    headers=self._get_headers(),
                    timeout=10,
                )
                response.raise_for_status()
                rows = response.json()
                if rows:
                    return {**rows[0], "type": "trip"}
                return None
        except Exception as e:
            logger.error(f"Erreur find_trip_by_source_url: {e}")
            return None

    async def find_city_by_source_url(self, normalized_url: str) -> Optional[Dict]:
        """Cherche une city existante par normalized_source_url. Retourne {id, city_title, type} ou None."""
        if not self.is_configured():
            return None
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self._get_url("cities"),
                    params={
                        "normalized_source_url": f"eq.{normalized_url}",
                        "limit": "1",
                        "select": "id,city_title",
                    },
                    headers=self._get_headers(),
                    timeout=10,
                )
                response.raise_for_status()
                rows = response.json()
                if rows:
                    return {**rows[0], "type": "city"}
                return None
        except Exception as e:
            logger.error(f"Erreur find_city_by_source_url: {e}")
            return None

    async def clone_trip_for_user(
        self, source_trip_id: str, job_id: str, user_id: Optional[str] = None
    ) -> Optional[str]:
        """Clone un trip existant (avec toutes ses relations) pour un nouvel utilisateur. Retourne le nouvel ID."""
        if not self.is_configured():
            return None

        def _do_clone() -> Optional[str]:
            import httpx as _httpx

            def _sb_get(table: str, params: dict) -> list:
                r = _httpx.get(
                    self._get_url(table),
                    params={**params, "select": "*"},
                    headers=self._get_headers(),
                    timeout=10,
                )
                r.raise_for_status()
                return r.json()

            def _sb_insert(table: str, payload: dict) -> dict:
                r = _httpx.post(
                    self._get_url(table),
                    json=payload,
                    headers=self._get_headers(),
                    timeout=10,
                )
                if not r.is_success:
                    logger.error("❌ %s → %s | body: %s", table, r.status_code, r.text)
                r.raise_for_status()
                rows = r.json()
                return rows[0] if rows else {}

            # 1. Fetch source trip
            trips = _sb_get("trips", {"id": f"eq.{source_trip_id}"})
            if not trips:
                logger.error(f"Trip source {source_trip_id} introuvable")
                return None
            src = trips[0]

            # 2. Create new trip (new id, new user, new job — same content)
            SKIP = {"id", "created_at", "updated_at", "duration_days"}
            new_trip_row = {k: v for k, v in src.items() if k not in SKIP}
            new_trip_row["job_id"] = job_id
            new_trip_row["user_id"] = user_id
            new_trip = _sb_insert("trips", new_trip_row)
            new_trip_id = new_trip["id"]
            logger.info(f"Trip cloné: {source_trip_id} → {new_trip_id}")

            # 3. Clone destinations (remap IDs pour les itinerary_days)
            old_dest_to_new: dict = {}
            for dest in _sb_get("destinations", {"trip_id": f"eq.{source_trip_id}"}):
                old_id = dest["id"]
                new_dest = {k: v for k, v in dest.items() if k not in SKIP}
                new_dest["trip_id"] = new_trip_id
                inserted = _sb_insert("destinations", new_dest)
                old_dest_to_new[old_id] = inserted["id"]

            # 4. Clone itinerary_days + spots
            for day in _sb_get("itinerary_days", {"trip_id": f"eq.{source_trip_id}"}):
                old_day_id = day["id"]
                new_day = {k: v for k, v in day.items() if k not in SKIP}
                new_day["trip_id"] = new_trip_id
                old_dest_id = day.get("destination_id")
                new_day["destination_id"] = old_dest_to_new.get(old_dest_id) if old_dest_id else None
                inserted_day = _sb_insert("itinerary_days", new_day)
                new_day_id = inserted_day["id"]

                for spot in _sb_get("spots", {"itinerary_day_id": f"eq.{old_day_id}"}):
                    new_spot = {k: v for k, v in spot.items() if k not in SKIP}
                    new_spot["itinerary_day_id"] = new_day_id
                    _sb_insert("spots", new_spot)

            # 5. Clone logistics
            for log in _sb_get("logistics", {"trip_id": f"eq.{source_trip_id}"}):
                new_log = {k: v for k, v in log.items() if k not in SKIP}
                new_log["trip_id"] = new_trip_id
                _sb_insert("logistics", new_log)

            # 6. Clone budget
            for budget in _sb_get("budgets", {"trip_id": f"eq.{source_trip_id}"}):
                new_budget = {k: v for k, v in budget.items() if k not in SKIP}
                new_budget["trip_id"] = new_trip_id
                _sb_insert("budgets", new_budget)

            # 7. Clone practical_info
            for practical in _sb_get("practical_info", {"trip_id": f"eq.{source_trip_id}"}):
                new_practical = {k: v for k, v in practical.items() if k not in SKIP}
                new_practical["trip_id"] = new_trip_id
                _sb_insert("practical_info", new_practical)

            logger.info(f"Trip {source_trip_id} cloné complètement → {new_trip_id} ✓")
            return new_trip_id

        try:
            return await asyncio.to_thread(_do_clone)
        except Exception as e:
            logger.error(f"Erreur clonage trip {source_trip_id}: {e}")
            return None

    async def clone_city_for_user(
        self, source_city_id: str, job_id: str, user_id: Optional[str] = None
    ) -> Optional[str]:
        """Clone une city existante (avec tous ses highlights) pour un nouvel utilisateur. Retourne le nouvel ID."""
        if not self.is_configured():
            return None

        def _do_clone() -> Optional[str]:
            import httpx as _httpx

            def _sb_get(table: str, params: dict) -> list:
                r = _httpx.get(
                    self._get_url(table),
                    params={**params, "select": "*"},
                    headers=self._get_headers(),
                    timeout=10,
                )
                r.raise_for_status()
                return r.json()

            def _sb_insert(table: str, payload: dict) -> dict:
                r = _httpx.post(
                    self._get_url(table),
                    json=payload,
                    headers=self._get_headers(),
                    timeout=10,
                )
                if not r.is_success:
                    logger.error("❌ %s → %s | body: %s", table, r.status_code, r.text)
                r.raise_for_status()
                rows = r.json()
                return rows[0] if rows else {}

            # 1. Fetch source city
            cities = _sb_get("cities", {"id": f"eq.{source_city_id}"})
            if not cities:
                logger.error(f"City source {source_city_id} introuvable")
                return None
            src = cities[0]

            # 2. Create new city
            SKIP = {"id", "created_at", "updated_at"}
            new_city_row = {k: v for k, v in src.items() if k not in SKIP}
            new_city_row["job_id"] = job_id
            new_city_row["user_id"] = user_id
            new_city = _sb_insert("cities", new_city_row)
            new_city_id = new_city["id"]
            logger.info(f"City clonée: {source_city_id} → {new_city_id}")

            # 3. Clone highlights
            for h in _sb_get("city_highlights", {"city_id": f"eq.{source_city_id}"}):
                new_h = {k: v for k, v in h.items() if k not in SKIP}
                new_h["city_id"] = new_city_id
                _sb_insert("city_highlights", new_h)

            # 4. Clone budget
            for budget in _sb_get("city_budgets", {"city_id": f"eq.{source_city_id}"}):
                new_budget = {k: v for k, v in budget.items() if k not in SKIP}
                new_budget["city_id"] = new_city_id
                _sb_insert("city_budgets", new_budget)

            # 5. Clone practical_info
            for practical in _sb_get("city_practical_info", {"city_id": f"eq.{source_city_id}"}):
                new_practical = {k: v for k, v in practical.items() if k not in SKIP}
                new_practical["city_id"] = new_city_id
                _sb_insert("city_practical_info", new_practical)

            logger.info(f"City {source_city_id} clonée complètement → {new_city_id} ✓")
            return new_city_id

        try:
            return await asyncio.to_thread(_do_clone)
        except Exception as e:
            logger.error(f"Erreur clonage city {source_city_id}: {e}")
            return None