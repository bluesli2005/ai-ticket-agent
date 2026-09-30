"""CSV exports preserve filter scope and spreadsheet-safe cell contents."""
import csv
import io
import tempfile
import threading
import unittest
import urllib.request
from app import MODEL, Server
from workspace import Workspace


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self.temp.name, MODEL)

    def tearDown(self):
        self.ws.close()
        self.temp.cleanup()

    def read_csv(self, **filters):
        data = self.ws.export_tickets(**filters)
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        return list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))

    def test_filters_and_multiline_fields(self):
        self.ws.create_ticket({'title': 'VPN,超时', 'description': '第一行\n"第二行"',
                               'category': '网络与连接'})
        self.ws.create_ticket({'title': '其他问题', 'description': '密码错误',
                               'category': '账号与认证'})
        rows = self.read_csv(search='VPN', status='待处理', category='网络与连接')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1:3], ['VPN,超时', '第一行\n"第二行"'])
        self.assertEqual(len(self.read_csv(search='无匹配')), 1)

    def test_formula_cells_are_text(self):
        self.ws.create_ticket({'title': '=1+1', 'description': '普通文本',
                               'requester': '  @SUM(1,2)'})
        row = self.read_csv()[1]
        self.assertEqual(row[1], "'=1+1")
        self.assertEqual(row[6], "'  @SUM(1,2)")

    def test_export_exceeds_list_limit(self):
        with self.ws.connect() as con:
            con.executemany('''INSERT INTO tickets
                (title,description,category,priority,created_at,updated_at)
                VALUES (?, '描述', '网络与连接', '普通', 'test', 'test')''',
                [(f'测试{i}',) for i in range(501)])
        self.assertEqual(len(self.ws.tickets()), 500)
        self.assertEqual(len(self.read_csv()), 502)

    def test_http_download_headers_and_scope(self):
        self.ws.create_ticket({'title': '导出测试', 'description': '仅隔离测试数据'})
        server = Server(('127.0.0.1', 0), self.ws)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/api/tickets/export?q=NO_MATCH'
            with urllib.request.urlopen(url) as response:
                self.assertIn('attachment;', response.headers['Content-Disposition'])
                self.assertIn('text/csv', response.headers['Content-Type'])
                rows = list(csv.reader(io.StringIO(response.read().decode('utf-8-sig'))))
                self.assertEqual(len(rows), 1)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
