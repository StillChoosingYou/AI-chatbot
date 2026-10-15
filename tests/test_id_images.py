import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from werkzeug.datastructures import MultiDict

from account_helpers import signed_client

from Chatbot import create_app


def fixture(color='red', metadata=False):
    stream=io.BytesIO()
    info=PngInfo()
    if metadata:
        info.add_text('Comment','PRIVATE TEST METADATA')
    Image.new('RGB',(60,90),color).save(stream,format='PNG',pnginfo=info)
    stream.seek(0)
    return stream


class IDImageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.database=Path(self.temp.name)/'inventory.sqlite3'
        self.app=create_app(self.database)
        self.client=signed_client(self.app)
        self.row=self.client.get('/api/inventory').json['records'][0]
        self.upload=f'/api/inventory/{self.row["id"]}/images'
        self.profile=f'/student/{self.row["token"]}'

    def tearDown(self):
        self.temp.cleanup()

    def post_upload(self, *args, **kwargs):
        response=self.client.post(*args, **kwargs)
        response.request.environ['wsgi.input'].close()
        return response

    def test_sample_sides_and_existing_database(self):
        with sqlite3.connect(self.database) as db:
            db.execute('DROP TABLE id_images')
        restored=signed_client(create_app(self.database))
        page=restored.get(self.profile).get_data(as_text=True)
        self.assertIn('Front of ID',page)
        self.assertIn('Back of ID',page)
        self.assertEqual(page.count('Sample design'),2)
        for side in ('front','back'):
            result=restored.get(f'{self.profile}/id/{side}.png')
            self.assertEqual(result.status_code,200)
            with Image.open(io.BytesIO(result.data)) as image:
                self.assertEqual(image.size,(640,1000))
            self.assertEqual(result.headers['Cache-Control'],'no-store')
        self.assertEqual(restored.get(f'{self.profile}/id/unknown.png').status_code,404)
        self.assertEqual(restored.get('/student/unknown/id/front.png').status_code,404)

    def test_back_qr_opens_the_same_student_record(self):
        with patch('id_cards.qrcode.QRCode.add_data') as add:
            response=self.client.get(f'{self.profile}/id/back.png')
        self.assertEqual(response.status_code,200)
        add.assert_called_once_with('http://localhost'+self.profile)

    def test_upload_both_sides_persist_strip_metadata_and_remove(self):
        result=self.post_upload(self.upload,data={'front':(fixture('red',True),'../../front.png'),
                                                'back':(fixture('blue'),'back.png')})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['saved'],['front','back'])
        restored=signed_client(create_app(self.database))
        for side,color in [('front',(255,0,0)),('back',(0,0,255))]:
            response=restored.get(f'{self.profile}/id/{side}.png')
            with Image.open(io.BytesIO(response.data)) as image:
                self.assertEqual(image.size,(60,90))
                self.assertEqual(image.getpixel((0,0)),color)
                self.assertNotIn('Comment',image.info)
        self.assertEqual(restored.get(self.profile).get_data(as_text=True).count('Uploaded image'),2)
        rows=restored.get('/api/inventory').json['records']
        row=next(row for row in rows if row['id']==self.row['id'])
        self.assertEqual(row['token'],self.row['token'])
        self.assertTrue(row['has_front_image'])
        self.assertTrue(row['has_back_image'])
        self.assertEqual(restored.delete(self.upload+'/front').status_code,200)
        self.assertIn('Sample design',restored.get(self.profile).get_data(as_text=True))
        with Image.open(io.BytesIO(restored.get(f'{self.profile}/id/back.png').data)) as image:
            self.assertEqual(image.getpixel((0,0)),(0,0,255))

    def test_jpeg_orientation_and_student_isolation(self):
        jpeg=io.BytesIO()
        image=Image.new('RGB',(30,60),'green')
        exif=Image.Exif()
        exif[274]=6
        exif[315]='TEST CAMERA METADATA'
        image.save(jpeg,format='JPEG',exif=exif)
        jpeg.seek(0)
        self.assertEqual(self.post_upload(self.upload,data={'front':(jpeg,'front.jpg')}).status_code,200)
        with Image.open(io.BytesIO(self.client.get(f'{self.profile}/id/front.png').data)) as stored:
            self.assertEqual(stored.size,(60,30))
            self.assertEqual(len(stored.getexif()),0)
        other=next(row for row in self.client.get('/api/inventory').json['records'] if row['id']!=self.row['id'])
        with Image.open(io.BytesIO(self.client.get(f'/student/{other["token"]}/id/front.png').data)) as stored:
            self.assertEqual(stored.size,(640,1000))

    def test_rejects_invalid_images_without_partial_replacement(self):
        self.post_upload(self.upload,data={'front':(fixture('red'),'front.png')})
        result=self.post_upload(self.upload,data={'front':(fixture('green'),'front.png'),
                                                'back':(io.BytesIO(b'<svg>not an image</svg>'),'fake.png')})
        self.assertEqual(result.status_code,400)
        with Image.open(io.BytesIO(self.client.get(f'{self.profile}/id/front.png').data)) as image:
            self.assertEqual(image.getpixel((0,0)),(255,0,0))
        for data in ({}, {'side':(fixture(),'other.png')}, {'front':(io.BytesIO(b'x'*(5*1024*1024+1)),'large.png')},
                     MultiDict([('front',(fixture(),'one.png')),('front',(fixture(),'two.png'))])):
            self.assertEqual(self.post_upload(self.upload,data=data).status_code,400)
        oversized=self.post_upload(self.upload,data=b'x'*(12*1024*1024+1),content_type='multipart/form-data')
        self.assertEqual(oversized.status_code,413)
        self.assertIn('error',oversized.json)
        self.assertEqual(self.post_upload('/api/inventory/99999/images',data={'front':(fixture(),'f.png')}).status_code,404)


if __name__=='__main__':
    unittest.main()
