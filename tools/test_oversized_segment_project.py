"""Full segment-plan workflow on an oversized synthetic project; no paid calls."""
import io
import json
import random
import unittest
from unittest.mock import patch
from PIL import Image

from console import gateway, llm, segment_plan, store
from tools.test_v2_segment_plan import SegmentFixture


class OversizedProjectTests(SegmentFixture, unittest.TestCase):
    def test_oversized_project_generates_and_persists_complete_plan(self):
        data = self.fixture()
        # Each frame is different and highly detailed; none can be discarded.
        for i, cell in enumerate(data['storyboard']['cells']):
            Image.frombytes('RGB', (1024, 1024), random.Random(i).randbytes(1024*1024*3)).save(self.d/cell['file'], quality=95)
        with patch.object(llm, '_IMAGE_CACHE', self.d/'cache'):
            request = segment_plan.messages(data, self.d)
            body = {'model':'offline', 'messages':request, 'max_tokens':8000}
            initial = len(json.dumps(body, ensure_ascii=False).encode())
            with patch.object(llm, '_REQUEST_IMAGE_STEPS', ((1024, 3),)):
                with self.assertRaises(llm.LLMError) as old:
                    llm._request_data(body)
            sent = []
            def response(req, **kw):
                sent.append(req.data)
                return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(self.value(),ensure_ascii=False)}}]}).encode())
            with patch('urllib.request.urlopen', side_effect=response), patch.object(llm, '_ssl_context', return_value=None):
                result = segment_plan.generate(self.pid)
            self.assertEqual(len(sent), 1)
            self.assertLessEqual(len(sent[0]), llm._REQUEST_LIMIT)
            self.assertEqual(len(llm._request_images(json.loads(sent[0]))), len(llm._request_images(body)))
            self.assertEqual(len(result['segments_plan']['segments']), 2)
            self.assertEqual(store.load(self.pid)['segments_plan'], result['segments_plan'])
            print(json.dumps({'project':self.pid, 'cells':8, 'images':len(llm._request_images(body)),
                              'initial_bytes':initial, 'old_compressed_bytes':old.exception.error_detail['request_bytes'],
                              'new_sent_bytes':len(sent[0]), 'saved_segments':2, 'remote_calls':'mocked'},ensure_ascii=False))


if __name__ == '__main__': unittest.main(verbosity=2)
