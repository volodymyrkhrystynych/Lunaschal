"""The full EXIF block kept on an uploaded photo (backend/food/exif.py).

`extract_photo_meta` answers when and where; `extract_exif_block` keeps
everything else the camera wrote. What is worth pinning down is mostly about
what it *refuses* to keep: Pillow hands back types JSON cannot hold, vendor
blobs that are kilobytes of undocumented binary, and — on a bad file — tags
whose values are junk. None of those may reach the row, and none of them may
cost the upload.
"""
import io
import json

from PIL import Image

from backend.food.exif import _fit_to_cap, extract_exif_block

_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825


def _jpeg(*, base=None, sub=None, gps=None, size=(8, 8)):
    img = Image.new('RGB', size, (120, 120, 120))
    exif = img.getexif()
    for tag, value in (base or {}).items():
        exif[tag] = value
    if sub:
        ifd = exif.get_ifd(_EXIF_IFD)
        for tag, value in sub.items():
            ifd[tag] = value
    if gps:
        ifd = exif.get_ifd(_GPS_IFD)
        for tag, value in gps.items():
            ifd[tag] = value
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    buf.seek(0)
    return buf


def _write(tmp_path, buf, name='photo.jpg'):
    path = tmp_path / name
    path.write_bytes(buf.getvalue())
    return path


def test_keeps_camera_tags_under_their_standard_names(tmp_path):
    path = _write(tmp_path, _jpeg(
        base={0x010F: 'Apple', 0x0110: 'iPhone 15 Pro', 0x0112: 1},
        sub={0x9003: '2026:03:14 09:41:02', 0x8827: 125},
    ))
    block = extract_exif_block(path)
    assert block['Make'] == 'Apple'
    assert block['Model'] == 'iPhone 15 Pro'
    assert block['Orientation'] == 1
    assert block['DateTimeOriginal'] == '2026:03:14 09:41:02'
    assert block['ISOSpeedRatings'] == 125


def test_dimensions_come_from_the_image_itself(tmp_path):
    # Present even when the camera wrote no size tag, because they are a
    # property of the picture rather than of the camera.
    path = _write(tmp_path, _jpeg(base={0x010F: 'Apple'}, size=(23, 11)))
    block = extract_exif_block(path)
    assert (block['ImageWidth'], block['ImageHeight']) == (23, 11)


def test_rationals_become_floats(tmp_path):
    """Pillow returns IFDRational for every fractional tag; json.dumps cannot
    serialize one, so a row written from an unconverted block would fail at the
    INSERT rather than at read time."""
    path = _write(tmp_path, _jpeg(sub={0x829D: 1.78, 0x920A: 6.765}))
    block = extract_exif_block(path)
    assert isinstance(block['FNumber'], float)
    assert abs(block['FNumber'] - 1.78) < 0.01
    assert abs(block['FocalLength'] - 6.765) < 0.01
    json.dumps(block)  # the point of the coercion


def test_gps_is_nested_under_its_own_key(tmp_path):
    path = _write(tmp_path, _jpeg(
        base={0x010F: 'Apple'},
        gps={1: 'N', 2: (43.0, 39.0, 11.0), 3: 'W', 4: (79.0, 22.0, 59.0)},
    ))
    block = extract_exif_block(path)
    assert block['GPS']['GPSLatitudeRef'] == 'N'
    assert block['GPS']['GPSLatitude'] == [43.0, 39.0, 11.0]


def test_vendor_blobs_are_dropped(tmp_path):
    """MakerNote is undocumented per-vendor binary and routinely kilobytes. It
    is the single biggest thing in a real block and means nothing to a reader."""
    path = _write(tmp_path, _jpeg(
        base={0x010F: 'Apple'}, sub={0x927C: b'\x01\x02\x03' * 400},
    ))
    block = extract_exif_block(path)
    assert 'MakerNote' not in block
    assert block['Make'] == 'Apple'


def test_a_photo_with_no_exif_has_no_block(tmp_path):
    """None rather than a dict of width/height: those two are always readable,
    so returning them would make every stripped image look like it carried
    metadata."""
    img = Image.new('RGB', (8, 8))
    buf = io.BytesIO()
    img.save(buf, 'JPEG')
    assert extract_exif_block(_write(tmp_path, buf)) is None


def test_an_unreadable_file_is_none_not_an_error(tmp_path):
    path = tmp_path / 'broken.jpg'
    path.write_bytes(b'not a jpeg at all')
    assert extract_exif_block(path) is None


def test_a_missing_file_is_none_not_an_error(tmp_path):
    assert extract_exif_block(tmp_path / 'absent.jpg') is None


def test_long_list_values_are_capped_too():
    """The per-value char cap only bites on strings. A tag holding thousands of
    rationals — legal, and what a corrupt file tends to produce — would sail
    past it, so the block cap is the backstop that has to catch it."""
    block = _fit_to_cap({
        'ImageWidth': 8, 'ImageHeight': 8, 'Make': 'Apple',
        'SubjectArea': list(range(20_000)),
    })
    assert len(json.dumps(block)) <= 16_384
    assert block['Make'] == 'Apple'
    assert (block['ImageWidth'], block['ImageHeight']) == (8, 8)


def test_dimensions_survive_the_cap():
    """They are dropped last precisely because they are the one part of the
    block that is never the thing making it too big."""
    block = _fit_to_cap({
        'ImageWidth': 8, 'ImageHeight': 8,
        'A': list(range(20_000)), 'B': list(range(20_000)),
    })
    assert (block['ImageWidth'], block['ImageHeight']) == (8, 8)


def test_oversized_blocks_stay_parseable(tmp_path):
    """Dropping the largest tags rather than truncating the JSON text: a
    truncated blob does not parse, and a row that cannot be read back is worse
    than one missing a tag nobody asked for."""
    path = _write(tmp_path, _jpeg(
        base={0x010F: 'Apple', 0x0131: 'x' * 40_000},
        sub={0x9003: '2026:03:14 09:41:02'},
    ))
    block = extract_exif_block(path)
    text = json.dumps(block)
    assert len(text) <= 16_384
    assert json.loads(text)['Make'] == 'Apple'
    assert block['ImageWidth'] == 8
