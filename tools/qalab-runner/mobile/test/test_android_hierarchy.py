"""Offline contract checks for real hierarchy selectors and native coordinate actions."""
import importlib.util
from pathlib import Path
import unittest

src = Path(__file__).resolve().parents[1] / "android-hierarchy.py"
spec = importlib.util.spec_from_file_location("android_native_hierarchy", src)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class Phone:
    serial = "phone"
    def __init__(self):
        self.front = "org.example.app"
        self.text = "草稿"
        self.focus = True
        self.positions = []
        self.gestures = []
        self.arg = {}
        self.field_exists = True

    def dump_hierarchy(self, compressed=False):
        change = "" if self.field_exists else "-gone"
        return f"""<hierarchy><node class="android.view.View" package="org.example.app" scrollable="true" enabled="true" bounds="[0,0][200,400]">
        <node class="android.widget.EditText" package="org.example.app" resource-id="org.example.app:id/edit{change}" focused="{str(self.focus).lower()}" enabled="true" bounds="[20,40][100,100]" text="{self.text}"/>
        <node class="android.widget.Button" package="org.example.app" content-desc="返回" enabled="true" bounds="[0,40][80,100]"/>
        <node class="android.widget.Button" package="keyboard" content-desc="返回" enabled="true" bounds="[5,30][90,110]"/>
        <node class="android.widget.TextView" package="org.example.app" text="快速回答"/>
        <node class="android.view.View" package="org.example.app"><node class="android.widget.TextView" package="org.example.app" text="实际答案"/></node>
        </node></hierarchy>"""
    def app_current(self): return {"package":self.front}
    def window_size(self): return (200,400)
    def click(self, x, y): self.positions.append((x,y)); self.focus = True
    def swipe(self, *args, **kwargs): self.gestures.append(args)
    def __call__(self, **kwargs):
        self.arg = kwargs
        phone = self
        class Editor:
            count = 1
            def set_text(self, s): phone.text = s
        return Editor()


class NativeTest(unittest.TestCase):
    def setUp(self):
        self.phone=Phone()
        self.driver=native.Driver(self.phone,'phone','org.example.app','adb')
    def locate(self, using, value):
        return self.driver.command('findAll', {'using':using,'value':value})
    def test_xpath_class_siblings_and_foreign_package_isolation(self):
        sel = "//android.widget.TextView[@text='快速回答']/following-sibling::*[1]/descendant-or-self::android.widget.TextView[@text!='']"
        [answer] = self.locate('xpath',sel)
        self.assertEqual(self.driver.command('text',{'id':answer}),'实际答案')
        [back] = self.locate('accessibility id','返回')
        self.driver.command('click',{'id':back})
        self.assertEqual(self.phone.positions, [(40,70)])
        self.assertEqual(self.driver.command('mobile',{'name':'queryAppState','appId':self.phone.front}),4)
    def test_text_targets_confirmed_editor_with_unicode(self):
        self.phone.focus=False
        [field] = self.locate('id','org.example.app:id/edit')
        self.driver.command('type',{'id':field,'text':'中文消息'})
        self.assertEqual(self.phone.text,'中文消息')
        self.assertEqual(self.phone.arg, {'packageName':'org.example.app','className':'android.widget.EditText',
                                          'focused':True,'resourceId':'org.example.app:id/edit'})
        self.driver.command('clear',{'id':field})
        self.assertEqual(self.phone.text,'')
    def test_stale_lookup_or_off_app_never_taps(self):
        [field] = self.locate('id','org.example.app:id/edit')
        self.phone.field_exists=False
        with self.assertRaisesRegex(ValueError, 'stale element reference'):
            self.driver.command('click',{'id':field})
        self.phone.front='other'
        with self.assertRaisesRegex(ValueError,'未处于前台'):
            self.driver.command('click',{'id':field})
        self.assertEqual(self.phone.positions,[])
    def test_scroll_uses_only_current_scrollable_bounds(self):
        [thread] = self.locate('xpath',"//*[@scrollable='true']")
        self.driver.command('mobile',{'name':'scrollGesture','elementId':thread,'direction':'up','percent':.8})
        [(x1,y1,x2,y2)] = self.phone.gestures
        self.assertEqual(x1,x2); self.assertTrue(0<y1<y2<400)
        [button] = self.locate('accessibility id','返回')
        with self.assertRaisesRegex(ValueError,'不可滚动'):
            self.driver.command('mobile',{'name':'scrollGesture','elementId':button,'direction':'up','percent':.8})

if __name__ == "__main__":
    unittest.main()
