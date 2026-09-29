import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from scripts import test_selector_audit as fixtures
from app.models import SelectorKey
from app.services import selector_images as images

class SelectorImageTests(unittest.TestCase):
    start=fixtures.AuditTests.start
    element=fixtures.AuditTests.element
    report=fixtures.AuditTests.report
    def setUp(self):
        fixtures.AuditTests.setUp(self)
        self.temp=tempfile.TemporaryDirectory()
        self.paths=patch.object(images,'UPLOADS',Path(self.temp.name));self.paths.start()
    def tearDown(self):
        self.paths.stop();self.temp.cleanup();fixtures.AuditTests.tearDown(self)
    def png(self):
        im=Image.new('RGB',(200,100),'red');im.paste('blue',(40,20,80,60))
        out=io.BytesIO();im.save(out,format='PNG');im.close();return out.getvalue()
    def key(self):
        row=SelectorKey(project_id=1,key='test_button',frame='shell',desc='[首页]-[首页]-[浏览]-[按钮]',candidates='[]')
        self.db.add(row);self.db.commit();return row
    def upload(self,row,data=None,revision=None):
        return self.client.post(f'/api/selectors/{row.id}/screenshot',params={'expected_revision':revision or images.image_revision(row)},files={'file':('image.png',self.png() if data is None else data,'image/png')})
    def test_upload_replace_delete_and_stale_edit(self):
        row=self.key();before=(row.desc,row.candidates,row.change_status);initial=images.image_revision(row)
        response=self.upload(row);self.assertEqual(response.status_code,200,response.text)
        data=response.json()['data'];self.assertEqual(data['screenshot_source'],'upload')
        self.db.refresh(row);old=images.UPLOADS/row.screenshot_path;self.assertTrue(old.exists())
        self.assertEqual((row.desc,row.candidates,row.change_status),before)
        self.assertEqual(self.upload(row,revision=initial).status_code,409)
        response=self.upload(row);self.assertEqual(response.status_code,200);self.assertFalse(old.exists())
        self.db.refresh(row);path=images.UPLOADS/row.screenshot_path
        response=self.client.delete(f'/api/selectors/{row.id}/screenshot',params={'expected_revision':images.image_revision(row)})
        self.assertEqual(response.status_code,200,response.text);self.assertFalse(path.exists())
        self.assertFalse(response.json()['data']['screenshot_url']);self.assertEqual(response.json()['data']['screenshot_source'],'deleted')
    def test_invalid_file_and_permission(self):
        row=self.key();self.assertEqual(self.upload(row,b'<svg>not image</svg>').status_code,422)
        self.user.is_platform_admin=False;self.db.commit()
        self.assertEqual(self.upload(row).status_code,403)
    def test_audit_rejects_images_and_ignores_legacy_image_metadata(self):
        pid=self.start()
        for endpoint in ('audit-screenshot?page_id=home','screenshot'):
            response=self.client.post(f'/api/probe/{pid}/{endpoint}',files={'file':('page.png',self.png(),'image/png')})
            self.assertEqual(response.status_code,410,response.text)
        self.assertFalse(images.audit_page_path(pid,'home').exists())
        el=self.element();el['screenshot_rect']={'x':20,'y':10,'w':20,'h':20}
        data={'audit_version':1,'pages':[{'id':'home','ready':True,'complete':True,'elements':[el],'pageSize':{'w':100,'h':50},'screenshot_uploaded':True}]}
        with patch.object(images,'read_image',side_effect=AssertionError('audit must not read images')):
            r=self.client.patch(f'/api/probe/{pid}',json={'result':data})
        self.assertEqual(r.status_code,200,r.text);self.db.expire_all()
        row=self.db.query(SelectorKey).one();self.assertEqual(row.screenshot_path,'');self.assertEqual(row.screenshot_source,'')
        self.assertEqual(list(images.UPLOADS.rglob('*.png')),[])
    def test_migration_adds_image_columns_idempotently(self):
        from sqlalchemy import create_engine, text
        from app.db import migrate
        legacy=create_engine('sqlite://')
        try:
            with legacy.begin() as conn:
                conn.execute(text('CREATE TABLE selector_key (id INTEGER PRIMARY KEY, desc TEXT)'))
                conn.execute(text("INSERT INTO selector_key VALUES (1,'original')"))
            with patch.object(migrate,'engine',legacy):
                migrate.ensure_selector_change_status_column();migrate.ensure_selector_change_status_column()
            with legacy.connect() as conn:
                self.assertEqual(tuple(conn.execute(text('SELECT desc,screenshot_path,screenshot_source FROM selector_key')).one()),('original','',''))
        finally:legacy.dispose()
    def test_invalid_crop_coordinates_are_not_saved(self):
        with Image.new('RGB',(200,100)) as im:
            for rect in ({'x':-1,'y':0,'w':10,'h':10},{'x':0,'y':0,'w':999,'h':10},{'x':float('nan'),'y':0,'w':10,'h':10},{}):
                self.assertIsNone(images.crop_element(im,{'w':100,'h':50},rect))
            self.assertIsNone(images.crop_element(im,{'w':100,'h':100},{'x':0,'y':0,'w':10,'h':10}))

if __name__=='__main__':unittest.main()
