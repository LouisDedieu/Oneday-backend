"""
Test carousel detection logic without dependencies
Run: python3 test_carousel_detection.py
"""

from enum import Enum

class ContentType(str, Enum):
    VIDEO = "video"
    CAROUSEL = "carousel"
    UNKNOWN = "unknown"

def _detect_content_type(info: dict) -> ContentType:
    """Copy of detection logic from downloader.py for isolated testing"""
    if 'entries' in info and info['entries']:
        entries = info['entries']
        if len(entries) > 1:
            image_extensions = {'jpg', 'jpeg', 'png', 'webp'}
            all_images = all(
                e.get('ext', '').lower() in image_extensions 
                for e in entries if e
            )
            if all_images:
                return ContentType.CAROUSEL
    
    carousel_indicators = [
        info.get('media_type') == 8,
        info.get('num_slides', 0) > 1,
        info.get('carousel_title') is not None,
        info.get('is_unified_collection') == True,
        info.get('_type') in ('playlist', 'multi_video'),
        'carousel' in (info.get('title') or '').lower(),
    ]
    
    if any(carousel_indicators):
        return ContentType.CAROUSEL
    
    return ContentType.VIDEO

def test_content_type_detection():
    print("Testing content type detection...\n")

    # Test 1: Video (no entries)
    video_info = {
        'title': 'My Travel Video',
        'ext': 'mp4',
        'duration': 120,
    }
    result = _detect_content_type(video_info)
    assert result == ContentType.VIDEO, f"Expected VIDEO, got {result}"
    print("✓ Test 1: Video detected correctly")

    # Test 2: Carousel with multiple images (entries format)
    carousel_info = {
        'title': 'My Carousel Post',
        'entries': [
            {'ext': 'jpg', 'title': 'Image 1'},
            {'ext': 'jpg', 'title': 'Image 2'},
            {'ext': 'png', 'title': 'Image 3'},
        ]
    }
    result = _detect_content_type(carousel_info)
    assert result == ContentType.CAROUSEL, f"Expected CAROUSEL, got {result}"
    print("✓ Test 2: Carousel with entries detected correctly")

    # Test 3: Instagram carousel (media_type = 8)
    instagram_carousel_info = {
        'title': 'Post by francetravelers',
        'media_type': 8,
        'ext': 'mp4',
    }
    result = _detect_content_type(instagram_carousel_info)
    assert result == ContentType.CAROUSEL, f"Expected CAROUSEL for media_type=8, got {result}"
    print("✓ Test 3: Instagram carousel (media_type=8) detected")

    # Test 4: Instagram carousel (num_slides)
    instagram_slides_info = {
        'title': 'Post by francetravelers',
        'num_slides': 5,
        'ext': 'mp4',
    }
    result = _detect_content_type(instagram_slides_info)
    assert result == ContentType.CAROUSEL, f"Expected CAROUSEL for num_slides>1, got {result}"
    print("✓ Test 4: Instagram carousel (num_slides) detected")

    # Test 5: Instagram carousel (carousel_title)
    instagram_carousel_title_info = {
        'title': 'Post by francetravelers',
        'carousel_title': 'My Travel Photos',
        'ext': 'mp4',
    }
    result = _detect_content_type(instagram_carousel_title_info)
    assert result == ContentType.CAROUSEL, f"Expected CAROUSEL for carousel_title, got {result}"
    print("✓ Test 5: Instagram carousel (carousel_title) detected")

    # Test 6: Single image (not a carousel)
    single_image_info = {
        'title': 'Single Image Post',
        'entries': [
            {'ext': 'jpg', 'title': 'Image 1'},
        ]
    }
    result = _detect_content_type(single_image_info)
    assert result == ContentType.VIDEO, f"Expected VIDEO for single image, got {result}"
    print("✓ Test 6: Single image treated as video")

    # Test 7: Empty entries
    empty_entries_info = {
        'title': 'Empty Post',
        'entries': []
    }
    result = _detect_content_type(empty_entries_info)
    assert result == ContentType.VIDEO, f"Expected VIDEO for empty entries, got {result}"
    print("✓ Test 7: Empty entries treated as video")

    # Test 8: Video with media_type != 8
    video_with_media_info = {
        'title': 'Regular Video',
        'media_type': 2,
        'ext': 'mp4',
    }
    result = _detect_content_type(video_with_media_info)
    assert result == ContentType.VIDEO, f"Expected VIDEO for media_type=2, got {result}"
    print("✓ Test 8: Regular video (media_type=2) not detected as carousel")

    # Test 9: num_slides = 1 (not a carousel)
    single_slide_info = {
        'title': 'Single Slide Post',
        'num_slides': 1,
        'ext': 'mp4',
    }
    result = _detect_content_type(single_slide_info)
    assert result == ContentType.VIDEO, f"Expected VIDEO for num_slides=1, got {result}"
    print("✓ Test 9: Single slide not detected as carousel")

    print("\n✅ All tests passed!")

if __name__ == "__main__":
    test_content_type_detection()
