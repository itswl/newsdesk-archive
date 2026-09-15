"""告警发送。

这里唯一真正重要的性质是：**发不出去必须被发现**。告警通道静默失效比没有
告警更糟——你以为有人会知道，其实没有。
"""
import json, unittest
from _base import bindir; bindir()
import alert


class TestBodyOk(unittest.TestCase):
    def test_lark_success(self):
        self.assertTrue(alert._body_ok(b'{"StatusCode":0,"code":0,"msg":"success"}'))

    def test_lark_error_with_http_200(self):
        # 实测形状：飞书/Lark 对错误 payload 返回 HTTP 200，失败写在 body 里。
        # 只看状态码的话通道断了也一直报「已发送」。
        self.assertFalse(alert._body_ok(b'{"code":19002,"msg":"params error, msg_type need"}'))

    def test_dingtalk_error(self):
        self.assertFalse(alert._body_ok(b'{"errcode":300001,"errmsg":"token is not exist"}'))

    def test_wecom_success(self):
        self.assertTrue(alert._body_ok(b'{"errcode":0,"errmsg":"ok"}'))

    def test_plain_text_ok_is_success(self):
        # Slack 返回纯文本 ok，不是 JSON——不瞎猜，按成功处理
        self.assertTrue(alert._body_ok(b'ok'))

    def test_empty_body_is_success(self):
        self.assertTrue(alert._body_ok(b''))

    def test_non_dict_json_is_success(self):
        self.assertTrue(alert._body_ok(b'[1,2,3]'))

    def test_non_numeric_code_does_not_crash(self):
        # code 不是数字时不能抛异常——告警失败绝不能反过来弄坏流水线
        self.assertTrue(alert._body_ok(b'{"code":"ok"}'))


class TestPayloadEscaping(unittest.TestCase):
    def test_quotes_and_newlines_do_not_break_json(self):
        # 正文来自失败日志，里面有引号和换行是常态。模板里 {{TEXT}} 位于
        # JSON 字符串内部，不按 JSON 规则转义就会把整个 payload 撑坏。
        tpl = '{"msg_type":"text","content":{"text":"{{TEXT}}"}}'
        body = '他说"炸了"\n第二行\t制表符\\反斜杠'
        built = tpl.replace('{{TEXT}}', json.dumps(body)[1:-1])
        self.assertEqual(json.loads(built)['content']['text'], body)


if __name__ == '__main__':
    unittest.main()
