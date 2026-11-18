"""MinerU adapter contracts, without requiring an external engine or real drawings."""

import os
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from workbench import extraction  # Set up the existing vendored OCR types.
from workbench.mineru_runtime import (
    ADVANCED_BACKEND, VLM_REPOSITORY, VLM_WEIGHTS, advanced_available,
    local_settings, parse_middle, recognize,
)


def span(text, kind='text'):
    return {'type': kind, 'content': text}


def text_block(text, bbox=None):
    return {'type': 'text', 'bbox': bbox, 'content': [span(text)]}


def middle(*blocks, tier='basic'):
    return {
        'schema': 'docvortex.middle', 'schema_version': '2.0',
        'metadata': {'producer': {'name': 'mineru', 'version': '4.2.3'}},
        'extensions': {
            'mineru': {'tier': tier, 'parse_mode': 'ocr'},
            'docvortex_layout': {
                'version': 1, 'pages': [{'page_idx': 0, 'width_pt': 400.0, 'height_pt': 200.0}],
            },
        },
        'pages': [{'page_idx': 0, 'blocks': list(blocks)}],
    }


class MineruGeometryTests(unittest.TestCase):
    def test_maps_measured_boxes_and_omits_unlocated_or_ungrounded_content(self):
        payload = middle(
            text_block('M8 × 1.25 − 6h', [.125, .25, .375, .5]),
            text_block('unlocated'),
            text_block('pretended pixel coordinates', [100, 80, 300, 160]),
            text_block('nonfinite', [0, 0, float('nan'), .5]),
            {'type': 'image', 'content': [{'type': 'image_body', 'content': 'image URI'}]},
        )
        result = parse_middle(payload, (800, 400), 'mineru_basic')
        self.assertEqual(result.text, 'M8 × 1.25 − 6h')
        self.assertEqual(len(result.regions), 1)
        self.assertEqual(result.regions[0].box, (100, 100, 300, 200))
        self.assertEqual(result.regions[0].layout_type, 'text')
        self.assertEqual(result.regions[0].confidence, 0)
        self.assertIn('3 個文字區塊', result.warnings[-1])

    def test_nested_table_text_has_its_actual_box_and_no_invented_cells(self):
        payload = middle({
            'type': 'table', 'content': [{
                'type': 'table_body', 'bbox': [.125, .5, .75, .875],
                'content': '<table><tr><td>規格</td><td>M5</td></tr>'
                           '<tr><td>數量</td><td>160 M</td></tr></table>',
            }],
        })
        result = parse_middle(payload, (800, 400), 'mineru_basic')
        self.assertEqual(result.text, '規格\tM5\n數量\t160 M')
        self.assertEqual([region.box for region in result.regions], [(100, 200, 600, 350)])
        self.assertEqual(result.layout_regions, [{'box': (100, 200, 600, 350), 'layout_type': 'table_body'}])

    def test_advanced_figure_geometry_is_separate_from_generated_figure_text(self):
        box = [.125, .5, .75, .875]
        payload = middle(
            text_block('標籤原文', [.125, .25, .375, .5]),
            {'type': 'image', 'bbox': box, 'content': [
                {'type': 'image_body', 'bbox': box, 'content': '生成敘述 數量:98765'},
                {'type': 'image_caption', 'bbox': box, 'content': [span('生成圖說 報價:88887')]},
            ]},
            {'type': 'image', 'bbox': None, 'content': [
                {'type': 'image_body', 'content': 'no source geometry'},
            ]},
            tier='advanced',
        )
        result = parse_middle(payload, (800, 400), ADVANCED_BACKEND)
        self.assertEqual(result.text, '標籤原文')
        self.assertEqual(len(result.regions), 1)
        self.assertEqual(result.layout_regions, [{'box': (100, 200, 600, 350), 'layout_type': 'image'}])

    def test_never_merges_surrounding_words_across_an_inline_formula(self):
        block = text_block('尺寸', [.125, .25, .375, .5])
        block['content'].extend((span('x^{2}', 'equation_inline'), span('毫米')))
        result = parse_middle(middle(block), (800, 400), 'mineru_basic')
        self.assertEqual(result.text, '尺寸\n毫米')

    def test_declines_version_tier_and_wrong_page_geometry(self):
        payload = middle()
        with self.assertRaisesRegex(ValueError, '層級'):
            parse_middle(payload, (800, 400), 'mineru_flash')
        with self.assertRaisesRegex(ValueError, '方向'):
            parse_middle(payload, (400, 800), 'mineru_basic')
        payload['metadata']['producer']['version'] = '4.2.4'
        with self.assertRaisesRegex(ValueError, '版本'):
            parse_middle(payload, (800, 400), 'mineru_basic')


class MineruSettingsTests(unittest.TestCase):
    def test_missing_optional_environment_does_not_resolve_a_path_binary(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'LOCALAPPDATA': folder, 'DRAWIQ_MINERU_PYTHON': '', 'DRAWIQ_MINERU_HOME': '',
        }):
            with self.assertRaisesRegex(RuntimeError, '找不到 MinerU 4.2.3'):
                local_settings()

    def test_explicit_local_paths_override_machine_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / 'python.exe'
            executable.touch()
            (root / 'models').mkdir()
            with patch.dict(os.environ, {
                'DRAWIQ_MINERU_PYTHON': str(executable), 'DRAWIQ_MINERU_HOME': str(root),
                'DRAWIQ_MINERU_ONNX_DEVICE': 'cpu',
            }):
                self.assertEqual(local_settings(), (executable, root, 'cpu'))

    def test_runner_does_not_inherit_remote_mineru_configuration(self):
        configuration = {}
        deadline = time.monotonic() + 15

        def simulate(command, *, cwd, env, deadline, log_path):
            self.assertEqual(command[0], 'C:/test/python.exe')
            self.assertNotIn('--remote', command)
            self.assertIn('--disable-image-analysis', command)
            self.assertEqual(env['MINERU_MODEL_SOURCE'], 'local')
            self.assertEqual(env['MINERU_TELEMETRY_DISABLED'], '1')
            self.assertNotIn('MINERU_API_URL', env)
            self.assertNotIn('MINERU_MODEL_VLM_SERVER_URL', env)
            configuration.update(json.loads(Path(env['MINERU_CONFIG']).read_text()))
            self.assertLess(deadline, time.monotonic() + 15)
            (cwd / 'page.json').write_text(json.dumps(middle(
                text_block('local result', [.125, .25, .375, .5])
            )), encoding='utf-8')
            return 'complete'

        with patch('workbench.mineru_runtime.local_settings', return_value=(
            'C:/test/python.exe', Path('C:/test/mineru'), 'auto',
        )), patch('workbench.mineru_runtime.supervise', side_effect=simulate), patch.dict(os.environ, {
            'MINERU_API_URL': 'https://example.invalid/',
            'MINERU_MODEL_VLM_SERVER_URL': 'https://example.invalid/v1',
        }):
            result = recognize(Image.new('RGB', (800, 400)), 'mineru_basic', deadline=deadline)
        self.assertEqual(result.text, 'local result')
        self.assertEqual(configuration['model']['source'], 'local')
        self.assertNotIn('vlm', configuration['model'])

    def test_advanced_requires_two_weights_and_keeps_image_analysis_local(self):
        def simulate(command, *, cwd, env, deadline, log_path):
            self.assertNotIn('--remote', command)
            self.assertNotIn('--disable-image-analysis', command)
            self.assertEqual(command[command.index('--tier') + 1], 'advanced')
            model = json.loads(Path(env['MINERU_CONFIG']).read_text())['model']
            self.assertEqual(model['source'], 'local')
            self.assertEqual(model['vlm'], {'engine': 'llama-cpp'})
            (cwd / 'page.json').write_text(json.dumps(middle(tier='advanced')), encoding='utf-8')
            return 'complete'

        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            repository = cache / 'models' / VLM_REPOSITORY
            repository.mkdir(parents=True)
            first, second = VLM_WEIGHTS
            sizes = {first: 1, second: 2}
            (repository / '.mineru_complete').write_text(json.dumps({'version': 1, 'files': sizes}), encoding='utf-8')
            (repository / first).write_bytes(b'a')
            with patch('workbench.mineru_runtime.VLM_WEIGHTS', sizes), patch(
                'workbench.mineru_runtime.local_settings', return_value=('C:/test/python.exe', cache, 'gpu')
            ):
                image = Image.new('RGB', (800, 400))
                self.assertFalse(advanced_available())
                with patch('workbench.mineru_runtime.supervise') as launch:
                    with self.assertRaisesRegex(RuntimeError, '缺少本機 VLM 權重'):
                        recognize(image, ADVANCED_BACKEND)
                    launch.assert_not_called()
                (repository / second).write_bytes(b'ab')
                self.assertTrue(advanced_available())
                with patch('workbench.mineru_runtime.supervise', side_effect=simulate):
                    result = recognize(image, ADVANCED_BACKEND)
                self.assertIn('Advanced (VLM/OCR)', result.engine)
