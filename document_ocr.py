"""Private, bounded OCR. Never validates authenticity or approves a driver."""
import io
import json
from contextlib import closing
import os
from pathlib import Path
import re
import shutil
import subprocess
import signal
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
MAX_PIXELS = 12_000_000
WORKER_TIMEOUT = 25

def configuration():
    local = ROOT / '.ocr/usr/bin/tesseract'
    binary = str(local) if local.exists() else shutil.which('tesseract')
    env = dict(os.environ, OMP_THREAD_LIMIT='1')
    if local.exists():
        env['LD_LIBRARY_PATH'] = str(ROOT / '.ocr/usr/lib/x86_64-linux-gnu') + ':' + env.get('LD_LIBRARY_PATH', '')
        env['TESSDATA_PREFIX'] = str(ROOT / '.ocr/usr/share/tesseract-ocr/5/tessdata')
    return binary, env

def candidates(text):
    dates = []
    from datetime import date
    for day, month, year in re.findall(r'\b(\d{2})[./-](\d{2})[./-](20\d{2})\b', text):
        try:
            value = date(int(year), int(month), int(day)).isoformat()
            if value not in dates:
                dates.append(value)
        except ValueError:
            pass
    for value in re.findall(r'\b20\d{2}-\d{2}-\d{2}\b', text):
        try:
            date.fromisoformat(value)
            if value not in dates:
                dates.append(value)
        except ValueError:
            pass
    return dates[:30]

def extract(content, mime):
    return _run_document(content, mime, 'extract')


def inspect_document(content, mime):
    """Check decodability, never official identity/authenticity or validity."""
    return _run_document(content, mime, 'inspect')


def _run_document(content, mime, operation):
    if not content or len(content) > 4 * 1024 * 1024:
        raise ValueError('Document vide ou supérieur à 4 Mo.')
    if mime not in ('application/pdf', 'image/png', 'image/jpeg'):
        raise ValueError('Format OCR non pris en charge.')
    if not LOCK.acquire(blocking=False):
        raise RuntimeError('Une analyse est déjà en cours. Réessayez.')
    try:
        if not configuration()[0]:
            raise RuntimeError('Le moteur OCR est indisponible.')
        # Isolate native PDF/image decoding as well as recognition. The web
        # process never parses an uploaded document. Kill the whole worker group
        # on timeout, including any Tesseract child, and delete temporary files.
        with tempfile.TemporaryDirectory(prefix='skg-ocr-worker-') as tmp:
            path = Path(tmp) / 'document'
            path.write_bytes(content)
            os.chmod(path, 0o600)
            worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                       '--worker', str(path), mime, operation],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      start_new_session=True)
            try:
                output, _ = worker.communicate(timeout=WORKER_TIMEOUT)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid, signal.SIGKILL)
                worker.communicate()
                raise RuntimeError('Analyse trop longue. Vérifiez le document manuellement.')
            if worker.returncode or len(output) > 160_000:
                raise ValueError('Document illisible ou limite de ressources dépassée.')
            result = json.loads(output)
            if not isinstance(result, dict) or result.get('manual_review_required') is not True:
                raise RuntimeError('Résultat OCR invalide.')
            return result
    finally:
        LOCK.release()


def _inspect_in_worker(content, mime):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    if mime == 'application/pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(content) as pdf:
            pages = len(pdf)
            if pages < 1 or pages > 20:
                raise ValueError('Le PDF doit contenir entre 1 et 20 pages.')
            for i in range(min(2, pages)):
                with closing(pdf[i]) as page:
                    width, height = page.get_size()
                    if width <= 0 or height <= 0:
                        raise ValueError('Dimensions PDF invalides.')
                    scale = min(1, (MAX_PIXELS / (width * height)) ** .5)
                    with closing(page.render(scale=scale)) as bitmap:
                        bitmap.to_pil().load()
    else:
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('Image trop grande.')
            image.verify()
        pages = 1
    return {'structurally_readable': True, 'pages': pages, 'manual_review_required': True}


def _extract_in_worker(content, mime):
        binary, env = configuration()
        if not binary:
            raise RuntimeError('Le moteur OCR est indisponible.')
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        images = []
        truncated = False
        if mime == 'application/pdf':
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(content) as pdf:
                if not len(pdf):
                    raise ValueError('PDF vide.')
                truncated = len(pdf) > 2
                for i in range(min(2, len(pdf))):
                    with closing(pdf[i]) as page:
                        width, height = page.get_size()
                        if width <= 0 or height <= 0:
                            raise ValueError('Dimensions PDF invalides.')
                        scale = min(2, (MAX_PIXELS / (width * height)) ** .5)
                        with closing(page.render(scale=scale)) as bitmap:
                            images.append(bitmap.to_pil().copy())
        elif mime in ('image/png', 'image/jpeg'):
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError('Image trop grande. Envoyez une image de moins de 12 mégapixels.')
                images.append(ImageOps.exif_transpose(image).convert('RGB'))
        else:
            raise ValueError('Format OCR non pris en charge.')
        texts = []
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='skg-ocr-') as tmp:
            for i, image in enumerate(images):
                image.thumbnail((2400, 2400))
                path = Path(tmp) / f'page-{i}.png'
                image.convert('RGB').save(path)
                remaining = max(.1, 20 - (time.monotonic() - started))
                result = subprocess.run([binary, str(path), 'stdout', '-l', 'fra+eng', '--psm', '6'],
                                        env=env, capture_output=True, timeout=remaining, check=True)
                texts.append(result.stdout.decode('utf-8', errors='replace')[:12000])
        text = '\n\n'.join(texts)
        return {'engine': 'Tesseract', 'text': text, 'date_candidates': candidates(text),
                'pages_processed': len(images), 'truncated': truncated, 'manual_review_required': True}

def smoke_test():
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new('RGB', (1800, 350), 'white')
    draw = ImageDraw.Draw(image)
    font_path = ROOT / '.ocr/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    font = ImageFont.truetype(str(font_path) if font_path.exists() else 'DejaVuSans.ttf', 48)
    draw.text((40, 50), 'SONINKARAGO TEST OCR\nASSURANCE 31/12/2027', fill='black', font=font)
    out = io.BytesIO()
    image.save(out, format='PNG')
    result = extract(out.getvalue(), 'image/png')
    assert 'SONINKARAGO' in result['text'], result['text']
    assert '2027-12-31' in result['date_candidates'], result
    print('OCR_SMOKE_OK: Tesseract, texte et date reconnus, français+anglais')

if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--worker':
        import resource
        # Bound a single native worker on the 512 MiB server. This is resource
        # isolation, not a sandbox that can authenticate an official document.
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (22, 22))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024,) * 2)
        os.umask(0o077)
        operation = {'extract': _extract_in_worker, 'inspect': _inspect_in_worker}[sys.argv[4]]
        print(json.dumps(operation(Path(sys.argv[2]).read_bytes(), sys.argv[3])))
    else:
        smoke_test()
