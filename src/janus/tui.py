from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.widgets import Frame, TextArea
from prompt_toolkit.styles import Style
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.table import Table
import io

class JanusTUI:
    def __init__(self, rpc_client, history_path: str = ".janus_history"):
        self.rpc_client = rpc_client
        self.console = Console(file=io.StringIO(), force_terminal=True, color_system="truecolor")
        
        # 使用 FileHistory 实现跨 Session 的命令记录
        self.history = FileHistory(history_path)

        # Input Buffer
        self.input_field = TextArea(
            height=3,
            prompt=self._prompt_for(self.rpc_client.default_gateway),
            style='class:input-field',
            multiline=False,
            accept_handler=self.handle_command,
            history=self.history  # 绑定历史记录
        )

        # Output Area (Logs)
        self.output_field = TextArea(style='class:output-field', focusable=False)

        # Status Area (Open Orders)
        self.status_control = FormattedTextControl(text=self.get_open_orders_text)
        self.status_window = Window(content=self.status_control, height=10, style="class:status")

        # Layout
        self.root_container = HSplit([
            Frame(self.status_window, title="Open Orders (Live)"),
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
            'input-field': '#00ff00',
            'output-field': '#cccccc',
        })

        self.app = Application(
            layout=self.layout,
            key_bindings=self.kb,
            style=self.style,
            full_screen=True,
            mouse_support=True,
            refresh_interval=1.0 # 1s 刷新一次 UI
        )

    def update_prompt(self, broker_name: str):
        """Update the input prompt to reflect current broker."""
        self.input_field.prompt = self._prompt_for(broker_name)
        if self.app.is_running:
            self.app.invalidate()

    @staticmethod
    def _prompt_for(broker_name: str) -> str:
        return f"({broker_name}) > "

    def log(self, message: str):
        """Append text to output area"""
        new_text = self.output_field.text + f"\n{message}"
        lines = new_text.split('\n')[-50:]
        self.output_field.buffer.document = Document('\n'.join(lines), cursor_position=len(new_text))

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
        for order in orders:
            table.add_row(
                order.vt_orderid,
                order.symbol,
                order.direction.name,
                str(order.price),
                f"{order.traded}/{order.volume}",
                order.status.name
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
