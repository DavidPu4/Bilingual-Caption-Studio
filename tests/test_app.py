"""UI checks run locally when Streamlit is installed; core CI needs no model download."""
import importlib.util
from pathlib import Path
import unittest


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit is not installed')
class AppTests(unittest.TestCase):
    def test_sample_edit_and_export(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        next(button for button in app.button if button.label == 'Open sample captions').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 2)
        self.assertEqual(len(app.get('download_button')), 5)
        app.text_area[0].input('大家好，欢迎来到我的项目演示。这个工具可以生成中文和英文字幕。你可以修改字幕，然后导出文件。')
        next(button for button in app.button if button.label == 'Calculate character error rate').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.metric[-1].value, '0.0%')
