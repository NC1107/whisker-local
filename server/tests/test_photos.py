from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from whisker_local import photos as photos_module
from whisker_local.photos import ImageTooLarge, NotAnImage, normalise

AUTH = {"Authorization": "Bearer right-key"}


def picture(fmt: str = "PNG", size=(40, 30), mode: str = "RGB", **save) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, "orange").save(out, fmt, **save)
    return out.getvalue()


def decoded(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


@pytest.fixture
def cat_id(client: TestClient) -> int:
    return client.post("/api/cats", json={"name": "Miso"}, headers=AUTH).json()["id"]


def test_a_png_comes_back_as_a_jpeg(client: TestClient, cat_id: int):
    response = client.put(f"/api/cats/{cat_id}/photo", content=picture("PNG"), headers=AUTH)

    assert response.status_code == 200
    assert response.json()["has_photo"] is True
    assert response.json()["photo_version"] > 0
    fetched = client.get(f"/api/cats/{cat_id}/photo", headers=AUTH)
    assert fetched.headers["content-type"] == "image/jpeg"
    assert decoded(fetched.content).format == "JPEG"


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "GIF"])
def test_each_accepted_format_is_converted(fmt: str):
    assert decoded(normalise(picture(fmt))).format == "JPEG"


def test_a_large_picture_is_scaled_down_keeping_its_shape():
    result = decoded(normalise(picture("JPEG", size=(3200, 1600))))
    assert result.size == (1600, 800)


def test_a_small_picture_is_not_scaled_up():
    assert decoded(normalise(picture("PNG", size=(40, 30)))).size == (40, 30)


def test_location_and_other_metadata_are_removed():
    exif = Image.Exif()
    exif[0x010F] = "SomeCamera"
    exif[0x8825] = {1: "N", 2: (52.0, 31.0, 12.0)}
    original = picture("JPEG", exif=exif.tobytes())
    assert Image.open(io.BytesIO(original)).getexif()

    result = Image.open(io.BytesIO(normalise(original)))

    assert not result.getexif()
    assert "exif" not in result.info
    assert b"SomeCamera" not in normalise(original)


def test_orientation_is_applied_before_it_is_dropped():
    exif = Image.Exif()
    exif[0x0112] = 6
    result = decoded(normalise(picture("JPEG", size=(60, 20), exif=exif.tobytes())))
    assert result.size == (20, 60)


def test_transparency_is_flattened_onto_white():
    out = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(out, "PNG")
    pixel = decoded(normalise(out.getvalue())).getpixel((5, 5))
    assert all(channel > 240 for channel in pixel)


@pytest.mark.parametrize(
    "junk",
    [b"", b"hello", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"%PDF-1.4 ...", b"\xff\xd8\xff"],
)
def test_anything_that_is_not_an_image_is_refused(junk: bytes):
    with pytest.raises(NotAnImage):
        normalise(junk)


def test_a_truncated_image_is_refused():
    whole = picture("PNG", size=(200, 200))
    with pytest.raises(NotAnImage):
        normalise(whole[: len(whole) // 2])


def test_a_picture_with_too_many_pixels_is_refused_before_it_is_decoded(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(photos_module, "MAX_DECODED_BYTES", 1000)
    with pytest.raises(ImageTooLarge):
        normalise(picture("PNG", size=(100, 100)))


def test_the_budget_counts_bytes_so_an_alpha_channel_costs_more():
    solid = picture("WEBP", size=(6000, 5900), mode="RGBA", lossless=True)
    assert len(solid) < 8 * 1024
    with pytest.raises(ImageTooLarge):
        normalise(solid)


def test_an_animated_picture_is_refused():
    frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
    out = io.BytesIO()
    frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(NotAnImage):
        normalise(out.getvalue())


def test_two_big_uploads_at_once_do_not_decode_at_once(monkeypatch: pytest.MonkeyPatch):
    import threading
    import time

    running = 0
    peak = 0
    guard = threading.Lock()
    real = photos_module._normalise

    def counted(data: bytes) -> bytes:
        nonlocal running, peak
        with guard:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        try:
            return real(data)
        finally:
            with guard:
                running -= 1

    monkeypatch.setattr(photos_module, "_normalise", counted)
    threads = [threading.Thread(target=normalise, args=(picture(),)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 1


def test_the_api_refuses_a_tiny_file_that_would_decode_huge(client: TestClient, cat_id: int):
    solid = picture("WEBP", size=(6000, 5900), mode="RGBA", lossless=True)
    response = client.put(f"/api/cats/{cat_id}/photo", content=solid, headers=AUTH)
    assert response.status_code == 413


def test_a_png_near_the_old_pixel_cap_is_refused(client: TestClient, cat_id: int):
    big = picture("PNG", size=(6000, 6000), mode="RGBA")
    response = client.put(f"/api/cats/{cat_id}/photo", content=big, headers=AUTH)
    assert response.status_code == 413


def test_a_decompression_bomb_header_is_refused():
    # A PNG header that claims 100000 x 100000 pixels, with no real data behind it.
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
    header += struct.pack(">IIBBBBB", 100_000, 100_000, 8, 2, 0, 0, 0) + b"\0\0\0\0"
    with pytest.raises((ImageTooLarge, NotAnImage)):
        normalise(header)


def test_the_api_says_why_a_file_was_refused(client: TestClient, cat_id: int):
    response = client.put(f"/api/cats/{cat_id}/photo", content=b"not a picture", headers=AUTH)
    assert response.status_code == 415
    assert "not a picture" in response.json()["detail"]
    assert client.get(f"/api/cats/{cat_id}/photo", headers=AUTH).status_code == 404


def test_the_api_refuses_a_huge_picture_with_a_413(
    client: TestClient, cat_id: int, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(photos_module, "MAX_DECODED_BYTES", 10)
    response = client.put(f"/api/cats/{cat_id}/photo", content=picture("PNG"), headers=AUTH)
    assert response.status_code == 413


def test_a_body_over_eight_megabytes_is_refused(client: TestClient, cat_id: int):
    body = b"x" * (8 * 1024 * 1024 + 1)
    response = client.put(f"/api/cats/{cat_id}/photo", content=body, headers=AUTH)
    assert response.status_code == 413


def test_a_chunked_body_over_eight_megabytes_is_cut_off(client: TestClient, cat_id: int):
    def chunks():
        for _ in range(9):
            yield b"x" * (1024 * 1024)

    response = client.put(f"/api/cats/{cat_id}/photo", content=chunks(), headers=AUTH)
    assert response.status_code == 413


def test_a_picture_just_under_the_limit_is_accepted(client: TestClient, cat_id: int):
    noisy = Image.effect_noise((1500, 1500), 90).convert("RGB")
    out = io.BytesIO()
    noisy.save(out, "PNG")
    assert 2 * 1024 * 1024 < len(out.getvalue()) < 8 * 1024 * 1024
    assert (
        client.put(f"/api/cats/{cat_id}/photo", content=out.getvalue(), headers=AUTH).status_code
        == 200
    )


def test_the_photo_limit_does_not_loosen_other_routes(client: TestClient):
    response = client.put("/api/cats/1/other", content=b"x" * 100_000, headers=AUTH)
    assert response.status_code == 413
    response = client.post("/api/cats", content=b"x" * 100_000, headers=AUTH)
    assert response.status_code == 413


def test_the_photo_needs_the_access_key(client: TestClient, cat_id: int):
    client.put(f"/api/cats/{cat_id}/photo", content=picture(), headers=AUTH)
    for call in (client.get, client.delete):
        assert call(f"/api/cats/{cat_id}/photo").status_code == 401
    assert client.put(f"/api/cats/{cat_id}/photo", content=picture()).status_code == 401
    assert (
        client.get(f"/api/cats/{cat_id}/photo", headers={"Authorization": "Bearer no"}).status_code
        == 401
    )


def test_delete_removes_the_photo(client: TestClient, cat_id: int):
    client.put(f"/api/cats/{cat_id}/photo", content=picture(), headers=AUTH)

    assert client.delete(f"/api/cats/{cat_id}/photo", headers=AUTH).status_code == 204

    assert client.get(f"/api/cats/{cat_id}/photo", headers=AUTH).status_code == 404
    assert client.get(f"/api/cats/{cat_id}", headers=AUTH).json()["has_photo"] is False
    assert client.delete(f"/api/cats/{cat_id}/photo", headers=AUTH).status_code == 204


def test_replacing_a_photo_changes_its_version(client: TestClient, cat_id: int, parts):
    first = client.put(f"/api/cats/{cat_id}/photo", content=picture(), headers=AUTH).json()
    config, _, _ = parts
    stored = config.photo_dir / f"{cat_id}.jpg"
    import os

    os.utime(stored, ns=(1_000_000_000, 1_000_000_000))
    second = client.put(
        f"/api/cats/{cat_id}/photo", content=picture(size=(50, 50)), headers=AUTH
    ).json()
    assert second["photo_version"] != first["photo_version"]


def test_a_missing_cat_is_a_404_for_every_photo_call(client: TestClient):
    assert client.put("/api/cats/99/photo", content=picture(), headers=AUTH).status_code == 404
    assert client.get("/api/cats/99/photo", headers=AUTH).status_code == 404
    assert client.delete("/api/cats/99/photo", headers=AUTH).status_code == 404


def test_the_file_is_named_only_from_the_integer_id(client: TestClient, cat_id: int, parts):
    config, _, _ = parts
    client.put(f"/api/cats/{cat_id}/photo", content=picture(), headers=AUTH)
    assert [p.name for p in config.photo_dir.iterdir()] == [f"{cat_id}.jpg"]
    assert config.photo_dir.parent == config.db_path.parent


def test_a_path_in_place_of_the_id_never_reaches_the_disk(client: TestClient, parts):
    config, _, _ = parts
    response = client.put("/api/cats/..%2F..%2Fescape/photo", content=picture(), headers=AUTH)
    assert response.status_code in (404, 405, 422)
    assert not config.photo_dir.exists() or list(config.photo_dir.iterdir()) == []


def test_status_shows_the_photo_flag_for_each_cat(client: TestClient, cat_id: int):
    client.put(f"/api/cats/{cat_id}/photo", content=picture(), headers=AUTH)
    [cat] = client.get("/api/status", headers=AUTH).json()["cats"]
    assert cat["has_photo"] is True
    assert cat["photo_version"] is not None


def test_a_failed_save_leaves_no_partial_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    folder = tmp_path / "photos"
    store = photos_module.CatPhotos(folder)

    def fail(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        store.save(1, b"data")
    assert list(folder.iterdir()) == []
