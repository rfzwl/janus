from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame, TextArea
from prompt_toolkit.styles import Style
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.table import Table
import io
import queue

class JanusTUI:
    def __init__(self, rpc_client, history_path: str = ".janus_history"):
        self.rpc_client = rpc_client
        self.console = Console(file=io.StringIO(), force_terminal=True, color_system="truecolor")
        
        # 使用 FileHistory 实现跨 Session 的命令记录
        self.history = FileHistory(history_path)

        # Input Buffer
        self._prompt_provider = lambda: self._prompt_for(self.rpc_client.default_account)
        self.input_field = TextArea(
            height=Dimension(min=1, preferred=3, max=3),
            prompt=self._prompt_provider,
            style='class:input-field',
            multiline=False,
            accept_handler=self.handle_command,
            history=self.history  # 绑定历史记录
        )

        # Output Area (Logs)
        self.output_field = TextArea(
            style='class:output-field',
            focusable=False,
            height=Dimension(min=3, weight=2),
        )

        # Status Area (Open Orders)
        self.status_control = FormattedTextControl(text=self.get_open_orders_text)
        self.status_window = Window(
            content=self.status_control,
            height=Dimension(min=3, weight=1),
            style="class:status",
        )
        self.status_frame = Frame(
            self.status_window,
            title=self._orders_title_for(self.rpc_client.default_account),
        )

        # Positions Area
        self.positions_control = FormattedTextControl(text=self.get_positions_text)
        self.positions_window = Window(
            content=self.positions_control,
            height=Dimension(min=3, weight=1),
            style="class:positions",
        )
        self.positions_frame = Frame(
            self.positions_window,
            title=self._positions_title_for(self.rpc_client.default_account),
        )

        # Layout
        self.root_container = HSplit([
            self.status_frame,
            self.positions_frame,
            Frame(self.output_field, title="Logs"),
            Frame(self.input_field, title="Input"),
        ])

        self.layout = Layout(self.root_container)
        
        # Key bindings
        self.kb = KeyBindings()
        @self.kb.add('c-c')
        def _(event):
            event.app.exit()

        # Styles
        self.style = Style.from_dict({
            'status': 'bg:#222222 #ffffff',
            'positions': 'bg:#1e1e1e #ffffff',
            'input-field': '#00ff00',
            'output-field': '#cccccc',
        })

        self._log_queue = queue.Queue()

        self.app = Application(
            layout=self.layout,
            key_bindings=self.kb,
            style=self.style,
            full_screen=True,
            mouse_support=True,
            refresh_interval=1.0,  # 1s 刷新一次 UI
            before_render=lambda app: self._flush_log_queue(),
        )

    def update_prompt(self, account_name: str):
        """Update the input prompt to reflect current account."""
        self.input_field.prompt = self._prompt_provider
        self.status_frame.title = self._orders_title_for(account_name)
        self.positions_frame.title = self._positions_title_for(account_name)
        if self.app.is_running:
            self.app.invalidate()

    @staticmethod
    def _prompt_for(account_name: str) -> str:
        return f"({account_name}) > "

    @staticmethod
    def _positions_title_for(account_name: str) -> str:
        return f"Positions ({account_name})"

    @staticmethod
    def _orders_title_for(account_name: str) -> str:
        return f"Open Orders ({account_name})"

    def log(self, message: str):
        """Append text to output area"""
        self._log_queue.put(message)
        if self.app.is_running:
            self.app.invalidate()
        else:
            self._flush_log_queue()

    def _flush_log_queue(self):
        messages = []
        while True:
            try:
                messages.append(self._log_queue.get_nowait())
            except queue.Empty:
                break

        if not messages:
            return

        current_text = self.output_field.text
        if current_text:
            new_text = current_text + "\n" + "\n".join(messages)
        else:
            new_text = "\n".join(messages)

        lines = new_text.split('\n')[-50:]
        trimmed_text = '\n'.join(lines)
        self.output_field.buffer.document = Document(
            trimmed_text,
            cursor_position=len(trimmed_text),
        )

    def get_open_orders_text(self):
        """Generate Rich Table string for prompt_toolkit"""
        f = io.StringIO()
        console = Console(file=f, force_terminal=False, width=120)
        
        table = Table(title=None, show_edge=False, box=None)
        table.add_column("Order ID", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="magenta")
        table.add_column("Direction", style="green")
        table.add_column("Price", justify="right")
        table.add_column("Volume", justify="right")
        table.add_column("Status", style="yellow")

        orders = self.rpc_client.get_open_orders()
        if not orders:
            table.add_row("No open orders", "", "", "", "", "")
        for order in orders:
            direction = order.direction.name if order.direction else "-"
            table.add_row(
                order.vt_orderid,
                order.symbol,
                direction,
                str(order.price),
                f"{order.traded}/{order.volume}",
                order.status.name
            )
        
        console.print(table)
        return f.getvalue()

    def get_positions_text(self):
        """Generate Rich Table string for positions"""
        f = io.StringIO()
        console = Console(file=f, force_terminal=False, width=120)

        table = Table(title=None, show_edge=False, box=None)
        table.add_column("Symbol", style="magenta", no_wrap=True)
        table.add_column("Qty", justify="right")
        table.add_column("Last Price", justify="right")
        table.add_column("Mkt Value", justify="right")
        table.add_column("Cost", justify="right")
        table.add_column("Diluted Cost", justify="right")
        table.add_column("Unrealized P&L", justify="right")

        def fmt(value):
            if value is None:
                return "-"
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return str(value)

        positions = self.rpc_client.get_positions()
        if not positions:
            table.add_row("No positions", "", "", "", "", "", "")
        for pos in positions:
            last_price = getattr(pos, "last_price", None)
            market_value = getattr(pos, "market_value", None)
            cost = getattr(pos, "cost", None)
            if cost is None and pos.price:
                cost = pos.price
            diluted_cost = getattr(pos, "diluted_cost", None)
            table.add_row(
                pos.symbol,
                fmt(pos.volume),
                fmt(last_price),
                fmt(market_value),
                fmt(cost),
                fmt(diluted_cost),
                fmt(pos.pnl),
            )

        console.print(table)
        return f.getvalue()

    def handle_command(self, buff: Buffer):
        text = buff.text.strip()
        if not text:
            return True
        
        self.log(f"> {text}")
        
        try:
            if text == "quit":
                self.app.exit()
            elif text == "exit":
                self.rpc_client.stop_remote_server()
                self.app.exit()
            else:
                self.rpc_client.process_command(text, self.log)
                
        except Exception as e:
            self.log(f"[Error] {e}")

        return False
