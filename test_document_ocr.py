import io
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageFont
import document_ocr as ocr

class DocumentOCR(unittest.TestCase):
    def test_real_pdf_and_jpeg_text_and_date(self):
        image = Image.new('RGB', (1800, 350), 'white')
        font_path = ocr.ROOT / '.ocr/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        font = ImageFont.truetype(str(font_path) if font_path.exists() else 'DejaVuSans.ttf', 48)
        ImageDraw.Draw(image).text((40, 50), 'SONINKARAGO ASSURANCE 31/12/2027', fill='black', font=font)
        for fmt, mime in (('PDF', 'application/pdf'), ('JPEG', 'image/jpeg')):
            with self.subTest(format=fmt):
                out = io.BytesIO(); image.save(out, fmt)
                result = ocr.extract(out.getvalue(), mime)
                self.assertIn('SONINKARAGO', result['text'])
                self.assertIn('2027-12-31', result['date_candidates'])

    def test_real_png_text_and_date(self):
        ocr.smoke_test()

    def test_real_pdf_rendering(self):
        image = Image.new('RGB', (500, 100), 'white')
        out = io.BytesIO(); image.save(out, 'PDF')
        result = ocr.extract(out.getvalue(), 'application/pdf')
        self.assertEqual(result['pages_processed'], 1)
        self.assertTrue(result['manual_review_required'])

    def test_pdf_page_limit_is_disclosed(self):
        image = Image.new('RGB', (500, 100), 'white')
        out = io.BytesIO(); image.save(out, 'PDF', save_all=True, append_images=[image, image])
        result = ocr.extract(out.getvalue(), 'application/pdf')
        self.assertEqual(result['pages_processed'], 2)
        self.assertTrue(result['truncated'])

    def test_fake_pdf_envelope_is_rejected_on_upload(self):
        import server
        import base64
        envelope=b'%PDF-1.4\nnot a real PDF\n%%EOF\n'
        with self.assertRaises(ValueError):
            server.validate_driver_document({'kind':'licence','content_base64':base64.b64encode(envelope).decode()})

    def test_readable_upload_still_requires_manual_review(self):
        image=Image.new('RGB',(400,100),'white');out=io.BytesIO();image.save(out,'PNG')
        result=ocr.inspect_document(out.getvalue(),'image/png')
        self.assertTrue(result['structurally_readable']);self.assertTrue(result['manual_review_required'])

    def test_invalid_dates_are_not_suggested(self):
        self.assertEqual(ocr.candidates('31/02/2027 31/12/2027 2027-12-31'), ['2027-12-31'])

    def test_size_and_format_limits(self):
        for data, mime in ((b'', 'image/png'), (b'x'*(4*1024*1024+1), 'image/png'), (b'x','text/html')):
            with self.assertRaises(ValueError): ocr.extract(data,mime)

    def test_busy_request_does_not_wait(self):
        with ocr.LOCK:
            with self.assertRaises(RuntimeError): ocr.extract(b'x','image/png')

    def test_bad_image_releases_slot(self):
        with self.assertRaises(Exception): ocr.extract(b'not an image','image/png')
        self.assertTrue(ocr.LOCK.acquire(blocking=False));ocr.LOCK.release()

    def test_missing_engine_is_explicit(self):
        with patch.object(ocr,'configuration',return_value=(None,{})):
            with self.assertRaises(RuntimeError): ocr.extract(b'x','image/png')

if __name__ == '__main__': unittest.main()
