from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


def crop_image_white_borders(image_path: Path) -> bytes:
    try:
        image = Image.open(image_path)
    except UnidentifiedImageError:
        return b''
    # Invert image (so that white is 0)
    invert_image: Image = ImageOps.invert(image)
    image_box = invert_image.getbbox()
    cropped: Image = image.crop(image_box)
    image.close()
    cropped_image_bytes = BytesIO()
    cropped.save(cropped_image_bytes, format='JPEG')
    return cropped_image_bytes.getvalue()
