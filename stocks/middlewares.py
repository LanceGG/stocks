"""Scrapy 中间件。"""

from urllib.parse import urlparse, urlunparse

from scrapy import signals
from twisted.internet.error import ConnectionDone, ConnectionLost, ConnectError, TCPTimedOutError

# push2 主站易断连，按稳定性排序
EASTMONEY_PUSH2_HOSTS = (
    "push2delay.eastmoney.com",
    "82.push2.eastmoney.com",
    "push2.eastmoney.com",
)


EASTMONEY_PUSH2HIS_HOSTS = (
    "push2his.eastmoney.com",
    "82.push2his.eastmoney.com",
)


class EastMoneyPush2Middleware:
    """push2 / push2his API 连接失败时自动切换备用域名。"""

    RETRY_EXCEPTIONS = (
        ConnectionLost,
        ConnectionDone,
        ConnectError,
        TCPTimedOutError,
    )

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def __init__(self, settings):
        self.push2_hosts = settings.getlist("EASTMONEY_PUSH2_HOSTS") or list(
            EASTMONEY_PUSH2_HOSTS
        )
        self.his_hosts = settings.getlist("EASTMONEY_PUSH2HIS_HOSTS") or list(
            EASTMONEY_PUSH2HIS_HOSTS
        )

    def _hosts_for_url(self, url: str) -> list[str] | None:
        if any(host in url for host in self.his_hosts):
            return self.his_hosts
        if any(host in url for host in self.push2_hosts):
            return self.push2_hosts
        return None

    def process_exception(self, request, exception, spider):
        if spider.name not in ("stock_capital_flow", "stock_quarterly_quote"):
            return None
        hosts = self._hosts_for_url(request.url)
        if not hosts:
            return None
        if not isinstance(exception, self.RETRY_EXCEPTIONS):
            return None

        host_idx = request.meta.get("push2_host_idx", 0) + 1
        if host_idx >= len(hosts):
            spider.logger.warning("东方财富 API 全部域名失败: %s", request.url[:120])
            return None

        parsed = urlparse(request.url)
        new_url = urlunparse(parsed._replace(netloc=hosts[host_idx]))
        spider.logger.debug(
            "东方财富切换域名 %s -> %s",
            parsed.hostname,
            hosts[host_idx],
        )
        return request.replace(
            url=new_url,
            meta={**request.meta, "push2_host_idx": host_idx},
            dont_filter=True,
        )


class StocksSpiderMiddleware:
    """Spider 中间件：可在响应进入 Spider 前后做处理。"""

    @classmethod
    def from_crawler(cls, crawler):
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_input(self, response, spider):
        return None

    def process_spider_output(self, response, result, spider):
        for i in result:
            yield i

    def process_spider_exception(self, response, exception, spider):
        pass

    async def process_start(self, start):
        async for item_or_request in start:
            yield item_or_request

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)


class StocksDownloaderMiddleware:
    """Downloader 中间件：可在请求发出/响应返回时做处理。"""

    @classmethod
    def from_crawler(cls, crawler):
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request, spider):
        return None

    def process_response(self, request, response, spider):
        return response

    def process_exception(self, request, exception, spider):
        pass

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)
