import json
import re
from urllib.parse import urlencode

import scrapy

from stocks.items import FundRankingItem

API_URL = "https://fund.eastmoney.com/data/rankhandler.aspx"


class FundSpider(scrapy.Spider):
    name = "fund"
    allowed_domains = ["fund.eastmoney.com"]

    custom_settings = {
        "DEFAULT_REQUEST_HEADERS": {
            "Referer": "https://fund.eastmoney.com/data/fundranking.html",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    }

    async def start(self):
        params = self._build_params(page_index=1)
        yield scrapy.Request(
            url=f"{API_URL}?{urlencode(params)}",
            callback=self.parse,
            meta={"page_index": 1},
        )

    def _build_params(self, page_index: int) -> dict:
        return {
            "op": self.settings.get("FUND_RANK_OP", "ph"),
            "dt": self.settings.get("FUND_RANK_DT", "kf"),
            "ft": self.settings.get("FUND_RANK_FT", "all"),
            "rs": self.settings.get("FUND_RANK_RS", ""),
            "gs": self.settings.get("FUND_RANK_GS", "0"),
            "sc": self.settings.get("FUND_RANK_SC", "sqjzf"),
            "st": self.settings.get("FUND_RANK_ST", "desc"),
            "sd": self.settings.get("FUND_RANK_SD", "2025-01-01"),
            "ed": self.settings.get("FUND_RANK_ED", "2026-03-31"),
            "qdii": self.settings.get("FUND_RANK_QDII", ""),
            "tabSubtype": self.settings.get("FUND_RANK_TAB_SUBTYPE", ",,,,,"),
            "pi": str(page_index),
            "pn": str(self.settings.getint("FUND_RANK_PN", 50)),
            "dx": self.settings.get("FUND_RANK_DX", "1"),
            "v": "0.1",
        }

    def parse(self, response):
        page_index = response.meta["page_index"]
        payload = self._extract_payload(response.text)
        if payload is None:
            self.logger.error("无法解析 API 响应: page=%s", page_index)
            return

        records = payload.get("datas") or []
        query_start_date = self.settings.get("FUND_RANK_SD", "2025-01-01")
        query_end_date = self.settings.get("FUND_RANK_ED", "2026-03-31")
        sort_column = self.settings.get("FUND_RANK_SC", "sqjzf")

        for record in records:
            fields = record.split(",")
            if len(fields) < 19:
                self.logger.warning("字段数量不足，已跳过: %s", record[:80])
                continue

            yield FundRankingItem(
                fund_code=fields[0],
                fund_name=fields[1],
                pinyin_abbr=fields[2],
                nav_date=self._empty_to_none(fields[3]),
                unit_nav=self._to_decimal(fields[4]),
                accumulated_nav=self._to_decimal(fields[5]),
                daily_growth_rate=self._to_decimal(fields[6]),
                week_1_rate=self._to_decimal(fields[7]),
                month_1_rate=self._to_decimal(fields[8]),
                month_3_rate=self._to_decimal(fields[9]),
                month_6_rate=self._to_decimal(fields[10]),
                year_1_rate=self._to_decimal(fields[11]),
                year_2_rate=self._to_decimal(fields[12]),
                year_3_rate=self._to_decimal(fields[13]),
                ytd_rate=self._to_decimal(fields[14]),
                since_inception_rate=self._to_decimal(fields[15]),
                establish_date=self._empty_to_none(fields[16]),
                purchase_status=self._to_int(fields[17]),
                interval_growth_rate=self._to_decimal(fields[18]),
                original_fee=self._empty_to_none(fields[19]) if len(fields) > 19 else None,
                fee_rate=self._empty_to_none(fields[20]) if len(fields) > 20 else None,
                discount=self._empty_to_none(fields[21]) if len(fields) > 21 else None,
                discounted_fee=self._empty_to_none(fields[22]) if len(fields) > 22 else None,
                query_start_date=query_start_date,
                query_end_date=query_end_date,
                sort_column=sort_column,
                page_index=page_index,
            )

        all_pages = int(payload.get("allPages") or 0)
        max_pages = self.settings.getint("FUND_RANK_MAX_PAGES", 0)
        target_pages = min(all_pages, max_pages) if max_pages > 0 else all_pages

        self.logger.info(
            "page %s/%s, records=%s, total=%s",
            page_index,
            target_pages,
            len(records),
            payload.get("allRecords"),
        )

        if page_index < target_pages:
            next_page = page_index + 1
            params = self._build_params(page_index=next_page)
            yield scrapy.Request(
                url=f"{API_URL}?{urlencode(params)}",
                callback=self.parse,
                meta={"page_index": next_page},
            )

    @staticmethod
    def _extract_payload(text: str):
        text = text.strip()
        if "var rankData" not in text:
            return None

        datas_match = re.search(r"datas:\[(.*)\],allRecords:(\d+)", text, re.S)
        if not datas_match:
            return None

        try:
            datas = json.loads(f"[{datas_match.group(1)}]")
        except json.JSONDecodeError:
            return None

        all_pages_match = re.search(r"allPages:(\d+)", text)
        page_index_match = re.search(r"pageIndex:(\d+)", text)

        return {
            "datas": datas,
            "allRecords": int(datas_match.group(2)),
            "allPages": int(all_pages_match.group(1)) if all_pages_match else 0,
            "pageIndex": int(page_index_match.group(1)) if page_index_match else 0,
        }

    @staticmethod
    def _empty_to_none(value):
        value = (value or "").strip()
        return value or None

    @staticmethod
    def _to_decimal(value):
        value = (value or "").strip()
        if not value or value == "--":
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _to_int(value):
        value = (value or "").strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None
