"""Selector UI images: validated uploads and one page capture cropped per element."""
import hashlib
import io
import math
import re
import uuid
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from fastapi import HTTPException

UPLOADS = Path(__file__).resolve().parents[2] / 'uploads'
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 24_000_000

def image_revision(row):
    return hashlib.sha256(((row.screenshot_path or '')+'|'+(row.screenshot_source or '')).encode()).hexdigest()[:20]

def read_image(data):
    if len(data) > MAX_BYTES:
        raise HTTPException(413, '图片不能超过 10 MB')
    try:
        im = Image.open(io.BytesIO(data))
        if im.format not in ('PNG','JPEG','WEBP') or im.width * im.height > MAX_PIXELS:
            raise ValueError('unsupported image')
        im.load()
        return im.convert('RGB')
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise HTTPException(422, '请上传有效的 PNG、JPEG 或 WebP 图片（最多2400万像素）')

def save_image(image, key_id):
    image.thumbnail((1600,1600))
    rel = f'selectors/{key_id}/{uuid.uuid4().hex}.png'
    path = UPLOADS / rel
    path.parent.mkdir(parents=True,exist_ok=True)
    image.save(path,format='PNG')
    return rel

def audit_page_path(probe_id, page_id):
    # page_id is checked against the server manifest by both callers.
    if not page_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in page_id):
        raise HTTPException(422, '巡检页面标识无效')
    return UPLOADS / 'selector-audits' / str(int(probe_id)) / (page_id+'.png')

def crop_element(image, page_size, rect):
    if not image or not isinstance(page_size,dict) or not isinstance(rect,dict): return None
    try:
        w,h=float(page_size['w']),float(page_size['h'])
        x,y,rw,rh=(float(rect[k]) for k in ('x','y','w','h'))
        if not all(math.isfinite(v) for v in (w,h,x,y,rw,rh)) or min(w,h,rw,rh)<=0: return None
        if x<0 or y<0 or x+rw>w+1 or y+rh>h+1: return None
        sx,sy=image.width/w,image.height/h
        if abs(sx-sy)>max(sx,sy)*0.05: return None
        box=(max(0,math.floor(x*sx)),max(0,math.floor(y*sy)),min(image.width,math.ceil((x+rw)*sx)),min(image.height,math.ceil((y+rh)*sy)))
        if box[2]<=box[0] or box[3]<=box[1]:return None
        return image.crop(box)
    except (ValueError,TypeError,KeyError,OverflowError):return None


def remove_image(relative):
    if not re.fullmatch(r'selectors/\d+/[0-9a-f]{32}\.png', relative or ''): return
    try: (UPLOADS / relative).unlink(missing_ok=True)
    except OSError: pass
