import unittest

from stock_agent.agents import news
from stock_agent.compliance import sanitize, sanitize_tree
from stock_agent.http import redact
from stock_agent.llm import parse_json


class ComplianceTest(unittest.TestCase):
    def test_directive_phrases_replaced(self):
        cases = [
            "HPSP 적극 매수 추천합니다",
            "지금 매수하세요",
            "비중 확대 권고",
            "목표주가 50,000원",
            "반드시 상승할 종목",
            "수익 보장",
        ]
        for text in cases:
            out = sanitize(text)
            for banned in ("매수 추천", "매수하세요", "비중 확대", "목표주가", "반드시", "수익 보장"):
                self.assertNotIn(banned, out, (text, out))

    def test_observation_text_untouched(self):
        text = "단기 저항선 돌파 시 후발 장비주로 수급이 확산될 가능성이 관찰됨. 41,000원 지지 여부는 참고 지표로 유의미함."
        self.assertEqual(sanitize(text), text)

    def test_no_false_positive_on_disappear(self):
        # '사라'는 '사라지다'와 겹치므로 치환 대상이 아님
        self.assertEqual(sanitize("매도 물량이 사라지는 흐름"), "매도 물량이 사라지는 흐름")

    def test_sanitize_tree(self):
        out = sanitize_tree({"a": ["매수하세요"], "b": 3})
        self.assertEqual(out["b"], 3)
        self.assertNotIn("매수하세요", out["a"][0])


class NewsFilterTest(unittest.TestCase):
    AD = ["리딩방", "무료 추천", "[광고]"]

    def test_is_ad(self):
        self.assertTrue(news.is_ad("급등주 무료 추천 받으세요", "", self.AD))
        self.assertTrue(news.is_ad("정상 제목", "카톡 리딩방 입장", self.AD))
        self.assertFalse(news.is_ad("HBM3E 공급 확대", "3분기 최대 실적 전망", self.AD))

    def test_key_sentences_strips_byline(self):
        body = "[서울=뉴시스]홍길동 기자 = SK하이닉스가 HBM 공급을 늘린다. 3분기 실적도 개선될 전망이다. 세 번째 문장이다..."
        out = news.key_sentences(body)
        self.assertTrue(out.startswith("SK하이닉스가"), out)
        self.assertIn("개선될 전망이다.", out)
        self.assertNotIn("세 번째", out)

    def test_sentiment(self):
        self.assertGreater(news.sentiment_score("대규모 공급계약 체결, 사상 최대 실적"), 0)
        self.assertLess(news.sentiment_score("유상증자 결정에 주가 급락"), 0)
        self.assertEqual(news.sentiment_score("주주총회 개최 안내"), 0.0)
        self.assertEqual(news.label(0.5), "호재")
        self.assertEqual(news.label(-0.5), "악재")

    def test_disclosure_sentiment(self):
        self.assertEqual(news.disclosure_sentiment("단일판매ㆍ공급계약체결"), 1.0)
        self.assertEqual(news.disclosure_sentiment("단일판매ㆍ공급계약해지"), -1.0)
        self.assertEqual(news.disclosure_sentiment("유상증자결정"), -1.0)
        self.assertEqual(news.disclosure_sentiment("기업설명회(IR)개최"), 0.0)


class MiscTest(unittest.TestCase):
    def test_redact(self):
        text = "GET https://opendart.fss.or.kr/api/list.json?crtfc_key=abc123&bgn_de=20260101"
        self.assertNotIn("abc123", redact(text))

    def test_parse_json_with_fence(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json('설명 {"a": 2} 끝'), {"a": 2})


if __name__ == "__main__":
    unittest.main()
