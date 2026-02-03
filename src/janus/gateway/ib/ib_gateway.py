from typing import Optional

from vnpy_ib.ib_gateway import IbGateway, IbApi


class JanusIbApi(IbApi):
    def updatePortfolio(
        self,
        contract,
        position,
        marketPrice,
        marketValue,
        averageCost,
        unrealizedPNL,
        realizedPNL,
        accountName,
    ) -> None:
        registry = getattr(self.gateway, "symbol_registry", None)
        if registry:
            try:
                sec_type = getattr(contract, "secType", None)
                if sec_type != "STK":
                    self.gateway.write_log(
                        f"IB holding skipped (non-equity): {contract.symbol} {sec_type}"
                    )
                else:
                    conid = getattr(contract, "conId", None)
                    if not conid:
                        self.gateway.write_log(
                            f"IB holding missing conId: {contract.symbol}"
                        )
                    else:
                        currency = getattr(contract, "currency", None)
                        if currency and currency.upper() != "USD":
                            self.gateway.write_log(
                                f"IB holding skipped (non-US): {contract.symbol} {currency}"
                            )
                        else:
                            registry.ensure_ib_symbol(
                                symbol=contract.symbol,
                                conid=conid,
                                currency=currency,
                            )
            except Exception as exc:
                self.gateway.write_log(
                    f"Symbol registry update failed for IB holding {contract.symbol}: {exc}"
                )

        super().updatePortfolio(
            contract,
            position,
            marketPrice,
            marketValue,
            averageCost,
            unrealizedPNL,
            realizedPNL,
            accountName,
        )


class JanusIbGateway(IbGateway):
    def __init__(self, event_engine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self.api = JanusIbApi(self)
        self.symbol_registry = None

    def connect(self, setting: dict) -> None:
        self.symbol_registry = setting.get("symbol_registry")

        host: Optional[str] = setting.get("host") or setting.get("TWS地址")
        port: Optional[int] = setting.get("port") or setting.get("TWS端口")
        client_id: Optional[int] = setting.get("client_id") or setting.get("客户号")
        account: str = setting.get("account") or setting.get("交易账户") or ""

        if host is None or port is None or client_id is None:
            raise ValueError("Missing IB connection settings (host/port/client_id)")

        mapped = {
            "TWS地址": host,
            "TWS端口": port,
            "客户号": client_id,
            "交易账户": account,
        }
        super().connect(mapped)
