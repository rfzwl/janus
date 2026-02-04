from typing import Optional, List
from threading import Event, Lock

from vnpy_ib.ib_gateway import IbGateway, IbApi
from ibapi.contract import Contract, ContractDetails


class JanusIbApi(IbApi):
    def __init__(self, gateway: IbGateway) -> None:
        super().__init__(gateway)
        self._harmony_lock = Lock()
        self._harmony_events: dict[int, Event] = {}
        self._harmony_results: dict[int, List[ContractDetails]] = {}

    def request_contract_details(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
        sec_type: str = "STK",
        timeout: float = 5.0,
    ) -> List[ContractDetails]:
        if not self.status:
            return []

        ib_contract = Contract()
        ib_contract.symbol = symbol
        ib_contract.exchange = exchange
        ib_contract.currency = currency
        ib_contract.secType = sec_type

        with self._harmony_lock:
            self.reqid += 1
            reqid = self.reqid
            event = Event()
            self._harmony_events[reqid] = event
            self._harmony_results[reqid] = []

        self.client.reqContractDetails(reqid, ib_contract)
        event.wait(timeout)

        with self._harmony_lock:
            results = self._harmony_results.pop(reqid, [])
            self._harmony_events.pop(reqid, None)

        return results

    def contractDetails(self, reqId: int, contractDetails: ContractDetails) -> None:
        if reqId in self._harmony_results:
            self._harmony_results[reqId].append(contractDetails)
        super().contractDetails(reqId, contractDetails)

    def contractDetailsEnd(self, reqId: int) -> None:
        event = self._harmony_events.get(reqId)
        if event:
            event.set()
        super().contractDetailsEnd(reqId)

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
