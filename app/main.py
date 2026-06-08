"""Desktop UI for BCH Thermal Stamps.

The unit is a single stamp. The app has two workspaces, switched with the big
buttons at the top: "Crear estampas" (design a visual template and create one
or many stamps from it) and "Administrar estampas" (pick a stamp from the list
and see its real printed image alongside its funding QR, address, private key,
on-chain balance, and recovery actions).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from app import wallet
    from app.qrgen import make_qr_image
    from app.renderer import StampDesign, render_stamp
    from app.storage import (
        DATA_DIR,
        STATUS_CREATED,
        STATUS_EMPTY,
        STATUS_FUNDED,
        STATUS_RECOVERED,
        Storage,
        StampRecord,
    )
else:
    from . import wallet
    from .qrgen import make_qr_image
    from .renderer import StampDesign, render_stamp
    from .storage import (
        DATA_DIR,
        STATUS_CREATED,
        STATUS_EMPTY,
        STATUS_FUNDED,
        STATUS_RECOVERED,
        Storage,
        StampRecord,
    )


APP_TITLE = "BCH Thermal Stamps"


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable container. `body` is where children go."""

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="#ffffff")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style="Panel.TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ThermalStampsApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x960")
        self.minsize(1120, 700)
        self.state("zoomed")
        self.configure(bg="#f2f4f5")

        self.storage = Storage()
        self.design = StampDesign()
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.m_funding_qr_photo: ImageTk.PhotoImage | None = None
        self.detail_image_photo: ImageTk.PhotoImage | None = None
        self.view_mode = "crear"
        self.create_label = tk.StringVar(value="")
        self.batch_count = tk.IntVar(value=10)
        self.selected_stamp_id: str | None = None
        self._wif_revealed = False
        self._busy = False

        self._vars: dict[str, tk.Variable] = {}
        self._text_widgets: dict[str, tk.Text] = {}
        self._build_style()
        self._build_layout()
        self._render_preview()
        self._refresh_stamps()

    def destroy(self) -> None:
        self.storage.close()
        super().destroy()

    # -- styling ---------------------------------------------------------------

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f2f4f5")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f2f4f5", foreground="#182025", font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background="#ffffff", foreground="#182025", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#ffffff", foreground="#111719", font=("Segoe UI", 15, "bold"))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#637077", font=("Segoe UI", 9))
        style.configure("Mono.TLabel", background="#ffffff", foreground="#182025", font=("Consolas", 9))
        style.configure("TButton", font=("Segoe UI", 10), padding=(10, 7))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 9))
        style.configure("Danger.TButton", font=("Segoe UI", 10), padding=(10, 7))
        style.map("Danger.TButton", foreground=[("!disabled", "#9b1c1c")])
        style.configure("Mode.TButton", font=("Segoe UI", 12, "bold"), padding=(22, 14))
        style.configure("ModeActive.TButton", font=("Segoe UI", 12, "bold"), padding=(22, 14),
                        background="#1f6feb", foreground="#ffffff")
        style.map("ModeActive.TButton",
                  background=[("active", "#1f6feb"), ("pressed", "#1f6feb"), ("disabled", "#1f6feb")],
                  foreground=[("active", "#ffffff"), ("pressed", "#ffffff"), ("disabled", "#ffffff")])
        style.configure("StatusBar.TLabel", background="#f2f4f5", foreground="#637077", font=("Segoe UI", 9))
        style.configure("TCheckbutton", background="#ffffff", foreground="#182025", font=("Segoe UI", 10))
        style.configure("Treeview", font=("Segoe UI", 9), rowheight=24)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    # -- layout ----------------------------------------------------------------

    def _build_layout(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        self._build_mode_bar(container)

        self.workspace = ttk.Frame(container)
        self.workspace.grid(row=1, column=0, sticky="nsew")
        self.workspace.columnconfigure(0, weight=1)
        self.workspace.rowconfigure(0, weight=1)
        self._build_crear_view(self.workspace)
        self._build_administrar_view(self.workspace)
        self.administrar_view.grid_remove()

        self.status_var = tk.StringVar(value="")
        ttk.Label(container, textvariable=self.status_var, style="StatusBar.TLabel", wraplength=1000).grid(
            row=2, column=0, sticky="ew", pady=(10, 0)
        )

    def _build_mode_bar(self, parent) -> None:
        # The two workspaces are mutually exclusive full views, so the switcher
        # between them is the app's primary navigation - kept big and always
        # visible rather than tucked into a menu or tab strip.
        bar = ttk.Frame(parent)
        bar.grid(row=0, column=0, sticky="w", pady=(0, 12))
        self.mode_buttons: dict[str, ttk.Button] = {}
        for i, (mode, text) in enumerate((("crear", "Crear estampas"), ("administrar", "Administrar estampas"))):
            btn = ttk.Button(bar, text=text, style="Mode.TButton", command=lambda m=mode: self._set_mode(m))
            btn.grid(row=0, column=i, sticky="w", padx=(0 if i == 0 else 12, 0))
            self.mode_buttons[mode] = btn
        self._update_mode_buttons()

    def _update_mode_buttons(self) -> None:
        for mode, btn in self.mode_buttons.items():
            btn.configure(style="ModeActive.TButton" if mode == self.view_mode else "Mode.TButton")

    def _set_mode(self, mode: str) -> None:
        if mode == self.view_mode:
            return
        self.view_mode = mode
        if mode == "crear":
            self.administrar_view.grid_remove()
            self.crear_view.grid()
        else:
            self.crear_view.grid_remove()
            self.administrar_view.grid()
            self._populate_manage_panel()
        self._update_mode_buttons()

    # -- "Crear estampas": template editor + live preview ----------------------

    def _build_crear_view(self, parent) -> None:
        view = ttk.Frame(parent)
        self.crear_view = view
        view.grid(row=0, column=0, sticky="nsew")
        view.columnconfigure(0, weight=0, minsize=300)
        view.columnconfigure(1, weight=1)
        view.columnconfigure(2, weight=0, minsize=300)
        view.rowconfigure(0, weight=1)

        left_wrap = ttk.Frame(view, style="Panel.TFrame")
        left_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_wrap.rowconfigure(0, weight=1)
        left_wrap.columnconfigure(0, weight=1)
        left_scroll = ScrollableFrame(left_wrap)
        left_scroll.grid(row=0, column=0, sticky="nsew")
        self.left_panel = left_scroll.body
        self.left_panel.configure(padding=14)
        self.left_panel.columnconfigure(0, weight=1)

        self.center_panel = ttk.Frame(view, padding=0)
        self.center_panel.grid(row=0, column=1, sticky="nsew")
        self.center_panel.columnconfigure(0, weight=1)
        self.center_panel.rowconfigure(1, weight=1)

        right_wrap = ttk.Frame(view, style="Panel.TFrame")
        right_wrap.grid(row=0, column=2, sticky="nsew", padx=(10, 0))
        right_wrap.rowconfigure(0, weight=1)
        right_wrap.columnconfigure(0, weight=1)
        self.create_panel = ttk.Frame(right_wrap, style="Panel.TFrame", padding=14)
        self.create_panel.grid(row=0, column=0, sticky="nsew")
        self.create_panel.columnconfigure(0, weight=1)

        self._build_template_panel()
        self._build_center_panel()
        self._build_create_panel()

    def _build_template_panel(self) -> None:
        p = self.left_panel
        ttk.Label(p, text="Plantilla", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(p, text="Define como se ve cada estampa.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 14))

        row = 2
        row = self._block_section(p, row, "title_enabled", "Titulo", [("title", "Texto")])
        row = self._block_section(p, row, "wallet_enabled", "QR instalar wallet", [("wallet_label", "Etiqueta"), ("wallet_qr_data", "Enlace (URL)")])
        row = self._block_section(p, row, "claim_enabled", "QR cobrar", [("claim_label", "Etiqueta")], note="El QR se completa con la clave de cada estampa.")
        row = self._block_section(p, row, "instructions_enabled", "Texto instructivo", [("instructions", "Instrucciones", "text")])
        row = self._block_section(p, row, "details_enabled", "Detalles", [("amount", "Monto (BCH)"), ("expiry", "Vencimiento"), ("footer_note", "Nota final")])
        self.left_panel.rowconfigure(row, weight=1)

    def _build_center_panel(self) -> None:
        # The preview is the centerpiece of "Crear estampas": it owns the entire
        # center column, top to bottom, and the stamp is centered within it.
        self.center_panel.rowconfigure(0, weight=0)
        self.center_panel.rowconfigure(1, weight=1)

        header = ttk.Frame(self.center_panel)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Vista previa termica 58 mm", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Ancho base: 384 px", foreground="#637077").grid(row=0, column=1, sticky="e")

        canvas_frame = ttk.Frame(self.center_panel, padding=8)
        canvas_frame.grid(row=1, column=0, sticky="nsew")
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)
        self.preview_canvas = tk.Canvas(canvas_frame, bg="#dfe5e7", highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        self.preview_canvas.bind("<Configure>", lambda _e: self._render_preview())

    def _build_create_panel(self) -> None:
        p = self.create_panel
        ttk.Label(p, text="Crear estampas", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            p,
            text="Cada estampa es una unidad propia: se crea de a una. "
                 "'Crear varias' es solo un atajo que repite lo mismo N veces.",
            style="Muted.TLabel",
            wraplength=300,
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        ttk.Label(p, text="Nombre (opcional)", style="Panel.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Entry(p, textvariable=self.create_label).grid(row=3, column=0, sticky="ew", pady=(3, 8))
        ttk.Button(p, text="Crear esta estampa", style="Primary.TButton", command=self._create_one).grid(row=4, column=0, sticky="ew")

        ttk.Separator(p).grid(row=5, column=0, sticky="ew", pady=10)

        batch_row = ttk.Frame(p, style="Panel.TFrame")
        batch_row.grid(row=6, column=0, sticky="ew")
        batch_row.columnconfigure(2, weight=1)
        ttk.Label(batch_row, text="Atajo: crear varias", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        spin = ttk.Spinbox(batch_row, from_=1, to=1000, textvariable=self.batch_count, width=5)
        spin.grid(row=0, column=1, sticky="w", padx=(8, 8))
        ttk.Button(batch_row, text="Crear varias", command=self._create_batch).grid(row=0, column=2, sticky="e")

        ttk.Separator(p).grid(row=7, column=0, sticky="ew", pady=14)
        ttk.Label(
            p,
            text="Para ver, cargar, cobrar o gestionar una estampa ya creada, anda a 'Administrar estampas'.",
            style="Muted.TLabel",
            wraplength=300,
        ).grid(row=8, column=0, sticky="w")
        p.rowconfigure(9, weight=1)

    # -- "Administrar estampas": pick a stamp, see and manage it for real -------

    def _build_administrar_view(self, parent) -> None:
        view = ttk.Frame(parent)
        self.administrar_view = view
        view.grid(row=0, column=0, sticky="nsew")
        view.columnconfigure(0, weight=0, minsize=280)
        view.columnconfigure(1, weight=1)
        view.rowconfigure(0, weight=1)

        self._build_stamp_list_panel(view)
        self._build_stamp_detail_panel(view)

    def _build_stamp_list_panel(self, parent) -> None:
        p = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        p.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        p.columnconfigure(0, weight=1)
        p.rowconfigure(2, weight=1)

        bar = ttk.Frame(p, style="Panel.TFrame")
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, text="Estampas", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="Actualizar", command=self._refresh_stamps).grid(row=0, column=1, sticky="e")
        ttk.Label(p, text="Hace clic en una para verla y gestionarla.", style="Muted.TLabel", wraplength=240).grid(
            row=1, column=0, sticky="w", pady=(2, 10)
        )

        table_frame = ttk.Frame(p, style="Panel.TFrame")
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("label", "amount", "status")
        self.stamps_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        headings = {"label": "Etiqueta", "amount": "Monto", "status": "Estado"}
        widths = {"label": 100, "amount": 80, "status": 76}
        for key in columns:
            self.stamps_tree.heading(key, text=headings[key])
            self.stamps_tree.column(key, width=widths[key], anchor="w")
        self.stamps_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.stamps_tree.yview)
        self.stamps_tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.stamps_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select_stamp())
        self.stamps_tree.bind("<Double-1>", lambda _e: self._open_image())

    def _build_stamp_detail_panel(self, parent) -> None:
        wrap = ttk.Frame(parent, style="Panel.TFrame", padding=16)
        wrap.grid(row=0, column=1, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        self.detail_empty = ttk.Label(
            wrap,
            text="Selecciona una estampa de la lista para verla, cargarla o cobrarla.",
            style="Muted.TLabel",
            anchor="center",
            justify="center",
            font=("Segoe UI", 12),
        )
        self.detail_empty.grid(row=0, column=0, sticky="nsew")

        content = ttk.Frame(wrap, style="Panel.TFrame")
        self.detail_content = content
        content.grid(row=0, column=0, sticky="nsew")
        # Column 0 hugs the stamp image (weight 0 -> it shrinks to exactly the
        # canvas width we set in _render_stamp_image, no wasted gray on the
        # sides). Column 1 (info + commands) takes ALL the freed lateral space.
        content.columnconfigure(0, weight=0)
        content.columnconfigure(1, weight=1, minsize=480)
        content.rowconfigure(0, weight=0)
        content.rowconfigure(1, weight=1)

        self.detail_title = ttk.Label(content, text="", font=("Segoe UI", 15, "bold"))
        self.detail_title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # Real rendered stamp on the left - what actually gets printed, QR codes
        # and all (no placeholders here, unlike "Crear estampas").
        image_frame = ttk.Frame(content, padding=(0, 0, 16, 0))
        image_frame.grid(row=1, column=0, sticky="nsew")
        image_frame.columnconfigure(0, weight=1)
        image_frame.rowconfigure(0, weight=1)
        self.detail_image_canvas = tk.Canvas(image_frame, bg="#dfe5e7", highlightthickness=0)
        self.detail_image_canvas.grid(row=0, column=0, sticky="nsew")
        self.detail_image_canvas.bind("<Configure>", lambda _e: self._render_stamp_image())

        info_outer = ttk.Frame(content, style="Panel.TFrame")
        info_outer.grid(row=1, column=1, sticky="nsew")
        info_outer.rowconfigure(0, weight=1)
        info_outer.columnconfigure(0, weight=1)
        info_scroll = ScrollableFrame(info_outer)
        info_scroll.grid(row=0, column=0, sticky="nsew")
        self.manage_body = info_scroll.body
        self.manage_body.configure(padding=(16, 0, 0, 0))
        self.manage_body.columnconfigure(0, weight=1)
        self._build_manage_body_contents()

        content.grid_remove()

    def _build_manage_body_contents(self) -> None:
        body = self.manage_body
        # The panel is wide now, so use that room: information on the left,
        # every action button collected into a column on the right.
        body.columnconfigure(0, weight=1, minsize=240)  # info (must fit the 220px QR)
        body.columnconfigure(1, weight=0, minsize=200)  # acciones

        # ---------- left column: informacion ----------
        info = ttk.Frame(body, style="Panel.TFrame")
        info.grid(row=0, column=0, sticky="new", padx=(0, 18))
        info.columnconfigure(0, weight=1)

        ttk.Label(info, text="Cargar esta estampa", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        qr_hint = ttk.Label(
            info,
            text="Escanea este QR con tu wallet: ya trae la direccion y el monto cargados.",
            style="Muted.TLabel",
            wraplength=320,
        )
        qr_hint.grid(row=1, column=0, sticky="w")
        self.m_funding_qr = ttk.Label(info)
        self.m_funding_qr.grid(row=2, column=0, sticky="w", pady=(8, 8))
        self.m_address = ttk.Label(info, text="", style="Mono.TLabel", wraplength=320)
        self.m_address.grid(row=3, column=0, sticky="w", pady=(0, 4))

        ttk.Separator(info).grid(row=4, column=0, sticky="ew", pady=14)

        ttk.Label(info, text="Clave privada (WIF)", style="Panel.TLabel").grid(row=5, column=0, sticky="w")
        wif_hint = ttk.Label(info, text="Es el secreto que va en el QR de cobro.", style="Muted.TLabel", wraplength=320)
        wif_hint.grid(row=6, column=0, sticky="w")
        self.m_wif = ttk.Label(info, text="", style="Mono.TLabel", wraplength=320)
        self.m_wif.grid(row=7, column=0, sticky="w", pady=(2, 4))

        ttk.Separator(info).grid(row=8, column=0, sticky="ew", pady=14)

        # Estado + saldo live at the BOTTOM now, and auto-load when a stamp is
        # selected - so it reads like the current result rather than a header.
        self.m_status = ttk.Label(info, text="", style="Panel.TLabel", font=("Segoe UI", 11, "bold"), wraplength=320)
        self.m_status.grid(row=9, column=0, sticky="w")
        self.m_balance = ttk.Label(info, text="", style="Muted.TLabel", wraplength=320)
        self.m_balance.grid(row=10, column=0, sticky="w", pady=(2, 0))

        # On-chain movements (entrada/salida con monto y fecha) get filled in
        # per stamp by _render_movements; empty for stamps with no activity.
        self.m_movements = ttk.Frame(info, style="Panel.TFrame")
        self.m_movements.grid(row=11, column=0, sticky="ew", pady=(2, 0))
        self.m_movements.columnconfigure(0, weight=1)

        # Let the wrapping text grow with the (now wide) info column.
        self._info_wrap_labels = [self.m_status, self.m_balance, qr_hint, self.m_address, wif_hint, self.m_wif]
        info.bind("<Configure>", self._on_info_resize)

        # ---------- right column: acciones ----------
        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.grid(row=0, column=1, sticky="new")
        actions.columnconfigure(0, weight=1)

        ttk.Label(actions, text="Acciones", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Button(actions, text="Copiar direccion", command=self._copy_address).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.m_reveal_btn = ttk.Button(actions, text="Mostrar clave", command=self._toggle_wif)
        self.m_reveal_btn.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(actions, text="Copiar clave", command=self._copy_wif).grid(row=3, column=0, sticky="ew", pady=(0, 6))

        ttk.Separator(actions).grid(row=4, column=0, sticky="ew", pady=10)

        ttk.Button(actions, text="Ver / imprimir imagen", command=self._open_image).grid(row=5, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(actions, text="Consultar saldo (online)", command=self._check_balance).grid(row=6, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(actions, text="Recuperar fondos", command=self._recover_funds).grid(row=7, column=0, sticky="ew", pady=(0, 6))

        ttk.Separator(actions).grid(row=8, column=0, sticky="ew", pady=10)

        ttk.Button(actions, text="Eliminar estampa", style="Danger.TButton", command=self._delete_stamp).grid(row=9, column=0, sticky="ew")

    def _on_info_resize(self, event: tk.Event) -> None:
        # Keep the wrapping text as wide as the info column (which now stretches);
        # guarded so we don't reconfigure on every redundant <Configure>.
        wrap = max(160, event.width - 6)
        for label in self._info_wrap_labels:
            if int(label.cget("wraplength")) != wrap:
                label.configure(wraplength=wrap)

    # -- template editing ------------------------------------------------------

    _AMOUNT_KEY_RE = re.compile(r"\d*\.?\d*")

    @staticmethod
    def _validate_amount_key(proposed: str) -> bool:
        """Only allow keystrokes that keep the amount field a plain decimal number."""
        return ThermalStampsApp._AMOUNT_KEY_RE.fullmatch(proposed) is not None

    @staticmethod
    def _amount_sats_hint(text: str) -> str:
        try:
            value = float(text) if text not in ("", ".") else 0.0
        except ValueError:
            return "≈ 0 sats"
        sats = round(value * 100_000_000)
        return f"≈ {sats:,} sats".replace(",", ".")

    def _block_section(self, parent, row, enabled_key, title, fields, note: str | None = None):
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=(0, 0, 0, 8))
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        enabled = tk.BooleanVar(value=getattr(self.design, enabled_key))
        self._vars[enabled_key] = enabled
        enabled.trace_add("write", lambda *_a: self._sync_and_preview())
        ttk.Checkbutton(frame, text=title, variable=enabled).grid(row=0, column=0, sticky="w")

        field_row = 1
        for field in fields:
            key = field[0]
            label = field[1]
            kind = field[2] if len(field) > 2 else "entry"
            ttk.Label(frame, text=label, style="Panel.TLabel").grid(row=field_row, column=0, sticky="w", pady=(5, 2))
            if kind == "text":
                widget = tk.Text(frame, height=4, wrap="word", font=("Segoe UI", 10), relief="solid", borderwidth=1)
                widget.insert("1.0", getattr(self.design, key))
                widget.grid(row=field_row + 1, column=0, sticky="ew")
                widget.bind("<KeyRelease>", lambda _e, name=key, item=widget: self._text_changed(name, item))
                self._text_widgets[key] = widget
            else:
                var = tk.StringVar(value=getattr(self.design, key))
                self._vars[key] = var
                var.trace_add("write", lambda *_a: self._sync_and_preview())
                entry = ttk.Entry(frame, textvariable=var)
                if key == "amount":
                    vcmd = (self.register(self._validate_amount_key), "%P")
                    entry.configure(validate="key", validatecommand=vcmd)
                entry.grid(row=field_row + 1, column=0, sticky="ew")
                if key == "amount":
                    sats_var = tk.StringVar(value=self._amount_sats_hint(var.get()))
                    ttk.Label(frame, textvariable=sats_var, style="Muted.TLabel").grid(
                        row=field_row + 2, column=0, sticky="w", pady=(2, 0)
                    )
                    var.trace_add("write", lambda *_a, v=var, sv=sats_var: sv.set(self._amount_sats_hint(v.get())))
                    field_row += 1
            field_row += 2
        if note:
            ttk.Label(frame, text=note, style="Muted.TLabel", wraplength=250).grid(row=field_row, column=0, sticky="w", pady=(4, 0))
        return row + 1

    def _text_changed(self, key: str, widget: tk.Text) -> None:
        setattr(self.design, key, widget.get("1.0", "end").strip())
        self._render_preview()

    def _sync_and_preview(self) -> None:
        self._sync_design_from_vars()
        self._render_preview()

    def _sync_design_from_vars(self) -> None:
        for key, var in self._vars.items():
            if hasattr(self.design, key):
                setattr(self.design, key, var.get())

    def _render_preview(self) -> None:
        if not hasattr(self, "preview_canvas"):
            return
        self._sync_design_from_vars()
        try:
            image = render_stamp(self.design, scale=1)
        except Exception as exc:
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(
                max(200, self.preview_canvas.winfo_width() // 2),
                max(160, self.preview_canvas.winfo_height() // 2),
                text=f"No se pudo generar la vista previa:\n{exc}",
                fill="#9b1c1c",
                font=("Segoe UI", 11, "bold"),
                justify="center",
            )
            return

        canvas_width = max(1, self.preview_canvas.winfo_width())
        canvas_height = max(1, self.preview_canvas.winfo_height())
        margin = 28
        available_height = max(80, canvas_height - margin * 2)
        available_width = max(80, canvas_width - margin * 2)
        scale = min(available_width / image.width, available_height / image.height, 2.6)
        preview = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.preview_canvas.delete("all")
        # The stamp is the centerpiece: center it both horizontally and
        # vertically in the available canvas, like a piece on a stage.
        x = canvas_width // 2
        y = canvas_height // 2
        self.preview_canvas.create_rectangle(
            x - preview.width // 2 - 8, y - preview.height // 2 - 8,
            x + preview.width // 2 + 8, y + preview.height // 2 + 8,
            fill="#ffffff", outline="#c6cfd3",
        )
        self.preview_canvas.create_image(x, y, image=self.preview_photo, anchor="center")

    # -- stamp creation --------------------------------------------------------

    def _create_one(self) -> None:
        if self._busy:
            return
        self._sync_design_from_vars()
        label = self.create_label.get().strip()
        design = StampDesign.from_dict(self.design.to_dict())
        try:
            record = self.storage.create_stamp(design, label=label)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"No se pudo crear la estampa:\n{exc}")
            return
        self._refresh_stamps()
        self._select_in_tree(record.id)
        self._set_status(f"Estampa creada: {record.address_short}")

    def _create_batch(self) -> None:
        if self._busy:
            return
        try:
            count = int(self.batch_count.get())
        except (tk.TclError, ValueError):
            count = 0
        if count < 1:
            messagebox.showinfo(APP_TITLE, "Ingresa una cantidad valida.")
            return
        if count > 1 and not messagebox.askyesno(APP_TITLE, f"Crear {count} estampas nuevas?"):
            return

        self._sync_design_from_vars()
        base_label = self.create_label.get().strip()
        design = StampDesign.from_dict(self.design.to_dict())

        def work():
            last_id = None
            for i in range(count):
                label = f"{base_label} {i + 1}".strip() if base_label else ""
                rec = self.storage.create_stamp(design, label=label)
                last_id = rec.id
                self.after(0, lambda n=i + 1: self._set_status(f"Creando estampas... {n}/{count}"))
            return last_id

        def done(last_id):
            self._refresh_stamps()
            if last_id:
                self._select_in_tree(last_id)
            self._set_status(f"{count} estampas creadas.")

        self._run_async(work, done, label=f"Creando {count} estampas...")

    # -- stamp list & selection ------------------------------------------------

    def _refresh_stamps(self) -> None:
        selected = self.selected_stamp_id
        for item in self.stamps_tree.get_children():
            self.stamps_tree.delete(item)
        for record in self.storage.list_stamps():
            self.stamps_tree.insert(
                "", "end", iid=record.id,
                values=(record.label or "-", record.amount, self._status_label(record.status)),
            )
        if selected and self.stamps_tree.exists(selected):
            self.stamps_tree.selection_set(selected)

    def _status_label(self, status: str) -> str:
        # The stored word "sin fondos" reads like "never had any"; for a stamp
        # that was funded and then emptied, "cobrada" is what's actually true.
        return {STATUS_EMPTY: "cobrada"}.get(status, status)

    def _select_in_tree(self, stamp_id: str) -> None:
        if not self.stamps_tree.exists(stamp_id):
            return
        # Programmatic selection (e.g. right after creating a stamp): drive the
        # panel ourselves with the select handler unbound, so it does NOT kick
        # off an automatic balance check - which would hit the network for a
        # brand-new empty stamp and, while it ran, block the next "Crear" click.
        self.stamps_tree.unbind("<<TreeviewSelect>>")
        self.selected_stamp_id = stamp_id
        self._wif_revealed = False
        self.stamps_tree.selection_set(stamp_id)
        self.stamps_tree.see(stamp_id)
        self._populate_manage_panel()
        self.after_idle(lambda: self.stamps_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select_stamp()))

    def _current_stamp(self) -> StampRecord | None:
        if not self.selected_stamp_id:
            return None
        return self.storage.get_stamp(self.selected_stamp_id)

    def _on_select_stamp(self) -> None:
        selection = self.stamps_tree.selection()
        if not selection:
            return
        changed = selection[0] != self.selected_stamp_id
        self.selected_stamp_id = selection[0]
        self._wif_revealed = False
        self._populate_manage_panel()
        # Auto-refresh the balance whenever the user picks a different stamp, so
        # they never have to press "Consultar saldo" to see current funds. Only
        # on a real change of selection: that skips the programmatic re-select
        # inside _refresh_stamps and so avoids an endless check -> refresh ->
        # re-select -> check loop. Silent, so being offline doesn't nag.
        if changed and not self._busy:
            self._check_balance(silent=True)

    def _clear_manage_panel(self) -> None:
        self.detail_content.grid_remove()
        self.detail_empty.grid()

    def _describe_stamp_status(self, record: StampRecord) -> tuple[str, str]:
        """Turn the raw status/balance fields into a plain-language story.

        The bare status word ("sin fondos") doesn't say whether a stamp ever
        held funds or what happened to them, which is exactly what's useful to
        know when managing one. The on-chain transaction count (`tx_count`) is
        what makes "tuvo fondos y ya los cobraron" a fact instead of a guess -
        even when the funds came and went between two balance checks, so the
        balance and the peak we happened to observe are both zero.
        """
        if record.status == STATUS_FUNDED:
            headline = "Financiada: tiene saldo listo para que lo cobren ahora mismo."
        elif record.status == STATUS_RECOVERED:
            headline = "Recuperada: vos retiraste el saldo de vuelta a tu wallet."
        elif record.status == STATUS_EMPTY:
            headline = "Cobrada: recibio fondos y ya fueron retirados de la estampa."
        elif record.checked_at:
            headline = "Sin uso: la consultaste y todavia no recibio fondos."
        else:
            headline = "Creada: todavia no se consulto si recibio fondos."

        if not record.checked_at:
            detail = "Saldo: sin consultar todavia."
        else:
            notes = []
            if record.tx_count > 0:
                movs = "movimiento" if record.tx_count == 1 else "movimientos"
                notes.append(f"{record.tx_count} {movs} en la red")
            elif record.peak_balance_sats > record.balance_sats:
                notes.append(f"pico {record.peak_balance_sats} sats")
            notes.append(f"ultima consulta: {record.checked_at}")
            detail = f"Saldo actual: {record.balance_sats} sats  (" + "; ".join(notes) + ")"
        return headline, detail

    def _populate_manage_panel(self) -> None:
        record = self._current_stamp()
        if record is None:
            self._clear_manage_panel()
            return
        self.detail_empty.grid_remove()
        self.detail_content.grid()
        self.detail_title.configure(text=record.label or f"Estampa {record.id[:8]}")
        headline, detail = self._describe_stamp_status(record)
        self.m_status.configure(text=headline)
        self.m_balance.configure(text=detail)
        self.m_address.configure(text=record.address)
        self.m_wif.configure(text="*" * 18)
        self.m_reveal_btn.configure(text="Mostrar clave")
        self._render_funding_qr(record)
        self._render_stamp_image()
        self._render_movements(record)

    def _render_movements(self, record: StampRecord) -> None:
        """Show the address's on-chain movements (entrada/salida, monto y fecha)
        below the status. Rebuilt from scratch each time since the number of
        rows varies per stamp."""
        for child in self.m_movements.winfo_children():
            child.destroy()
        try:
            movements = json.loads(record.history_json or "[]")
        except (ValueError, TypeError):
            movements = []
        if not movements:
            return

        wrap = int(self.m_status.cget("wraplength")) or 320
        total_in = sum(m.get("amount", 0) for m in movements if m.get("dir") == "in")
        header = "Movimientos"
        if total_in:
            header += f"  (recibio en total: {self._fmt_sats(total_in)} sats)"
        ttk.Label(self.m_movements, text=header, style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(10, 2))

        for i, mv in enumerate(movements):
            is_in = mv.get("dir") == "in"
            tag = "Entrada" if is_in else "Salida"
            sign = "+" if is_in else "-"
            line = f"{tag}:  {sign}{self._fmt_sats(mv.get('amount', 0))} sats     {self._format_movement_time(mv)}"
            row = ttk.Frame(self.m_movements, style="Panel.TFrame")
            row.grid(row=i + 1, column=0, sticky="ew", pady=(6, 0))
            row.columnconfigure(0, weight=1)
            ttk.Label(row, text=line, style="Panel.TLabel").grid(row=0, column=0, sticky="w")
            txid = mv.get("txid", "")
            ttk.Label(row, text=txid, style="Mono.TLabel", wraplength=wrap).grid(row=1, column=0, sticky="w")
            ttk.Button(row, text="Copiar txid", command=lambda t=txid: self._copy_txid(t)).grid(
                row=2, column=0, sticky="w", pady=(2, 0)
            )

    @staticmethod
    def _fmt_sats(value: int) -> str:
        return f"{int(value):,}".replace(",", ".")

    def _format_movement_time(self, mv: dict) -> str:
        ts = mv.get("time")
        if ts:
            try:
                return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError, OverflowError):
                pass
        block = mv.get("block")
        return f"bloque {block}" if block else "sin confirmar"

    def _copy_txid(self, txid: str) -> None:
        if txid:
            self._to_clipboard(txid)
            self._set_status("TXID copiado.")

    def _funding_uri(self, record: StampRecord) -> str:
        """BIP21-style payment URI: address plus amount, when the amount is a clean number.

        Wallets that understand it pre-fill the exact amount to send. If the
        amount isn't a plain number (e.g. a stamp created before this existed),
        we fall back to the bare address - still a fully working funding QR,
        just without the amount pre-filled.
        """
        try:
            float(record.amount)
        except (TypeError, ValueError):
            return record.address
        return f"{record.address}?amount={record.amount}"

    def _render_funding_qr(self, record: StampRecord) -> None:
        try:
            qr_image = make_qr_image(self._funding_uri(record), target_px=220, high_ec=False)
        except Exception:
            self.m_funding_qr.configure(image="", text="(no se pudo generar el QR de fondeo)")
            self.m_funding_qr_photo = None
            return
        self.m_funding_qr_photo = ImageTk.PhotoImage(qr_image)
        self.m_funding_qr.configure(image=self.m_funding_qr_photo, text="")

    # Stamp is tall and narrow, so we fit it to the canvas HEIGHT; this cap stops
    # it from growing so wide on a tall window that it crowds out the info panel.
    DETAIL_MAX_STAMP_WIDTH = 460
    DETAIL_IMAGE_MARGIN = 20

    def _render_stamp_image(self) -> None:
        """Show the stamp's real, already-rendered image (real QR codes, not
        placeholders), and shrink the canvas to hug it so there's no wasted gray
        on the sides - that freed lateral space goes to the info/commands panel."""
        if not hasattr(self, "detail_image_canvas"):
            return
        canvas = self.detail_image_canvas
        margin = self.DETAIL_IMAGE_MARGIN
        record = self._current_stamp()
        if record is None:
            canvas.delete("all")
            self._fit_detail_canvas_width(360)
            return
        try:
            image = Image.open(record.image_path)
        except Exception as exc:
            canvas.delete("all")
            self._fit_detail_canvas_width(360)
            canvas.create_text(
                180, max(160, canvas.winfo_height() // 2),
                text=f"No se pudo abrir la imagen de la estampa:\n{exc}",
                fill="#9b1c1c",
                font=("Segoe UI", 11, "bold"),
                justify="center",
                width=320,
            )
            return

        canvas_height = max(1, canvas.winfo_height())
        available_height = max(80, canvas_height - margin * 2)
        # Fit to height, but never wider than the cap (protects the info panel).
        scale = min(available_height / image.height, self.DETAIL_MAX_STAMP_WIDTH / image.width)
        shown = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        self.detail_image_photo = ImageTk.PhotoImage(shown)

        # Hug the canvas to the stamp's width: column 0 has weight 0, so the
        # whole column collapses to this, and the leftover width flows to the
        # info panel instead of being shown as empty gray bands.
        target_width = shown.width + margin * 2
        self._fit_detail_canvas_width(target_width)

        canvas.delete("all")
        x = target_width // 2
        y = canvas_height // 2
        canvas.create_rectangle(
            x - shown.width // 2 - 8, y - shown.height // 2 - 8,
            x + shown.width // 2 + 8, y + shown.height // 2 + 8,
            fill="#ffffff", outline="#c6cfd3",
        )
        canvas.create_image(x, y, image=self.detail_image_photo, anchor="center")

    def _fit_detail_canvas_width(self, width: int) -> None:
        """Resize the stamp canvas, but only when it actually changed - resizing
        re-fires <Configure> (which calls us again), so the guard breaks that loop."""
        canvas = self.detail_image_canvas
        if abs(canvas.winfo_width() - width) > 2:
            canvas.configure(width=width)

    # -- manage actions --------------------------------------------------------

    def _copy_address(self) -> None:
        record = self._current_stamp()
        if record:
            self._to_clipboard(record.address)
            self._set_status("Direccion copiada.")

    def _toggle_wif(self) -> None:
        record = self._current_stamp()
        if not record:
            return
        self._wif_revealed = not self._wif_revealed
        if self._wif_revealed:
            self.m_wif.configure(text=record.wif)
            self.m_reveal_btn.configure(text="Ocultar clave")
        else:
            self.m_wif.configure(text="*" * 18)
            self.m_reveal_btn.configure(text="Mostrar clave")

    def _copy_wif(self) -> None:
        record = self._current_stamp()
        if record:
            self._to_clipboard(record.wif)
            self._set_status("Clave privada copiada al portapapeles.")

    def _open_image(self) -> None:
        record = self._current_stamp()
        if not record:
            return
        path = Path(record.image_path)
        if not path.exists():
            messagebox.showinfo(APP_TITLE, "No se encontro la imagen de la estampa.")
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"No se pudo abrir la imagen:\n{exc}")

    def _check_balance(self, silent: bool = False) -> None:
        record = self._current_stamp()
        if not record or self._busy:
            return
        prev_balance = record.balance_sats
        peak_balance = record.peak_balance_sats
        prev_status = record.status
        stamp_id = record.id
        wif = record.wif

        def work():
            return wallet.get_address_history(wif)

        def done(history):
            balance = history.balance_sats
            # Classify from on-chain reality, not just the current balance:
            # tx history reveals a stamp that was funded and emptied between
            # checks (balance back to 0, but it clearly *was* used).
            was_used = history.ever_used or peak_balance > 0 or prev_balance > 0
            if balance > 0:
                status = STATUS_FUNDED
            elif prev_status == STATUS_RECOVERED:
                status = STATUS_RECOVERED  # we already swept it ourselves; keep that
            elif was_used:
                status = STATUS_EMPTY  # funded at some point, now emptied (claimed)
            else:
                status = STATUS_CREATED  # no on-chain activity at all
            history_json = json.dumps([
                {"txid": m.txid, "dir": m.direction, "amount": m.amount_sats, "block": m.block, "time": m.time}
                for m in history.movements
            ])
            self.storage.record_balance_check(
                stamp_id, status, balance, max(peak_balance, balance), history.tx_count, history_json
            )
            self._refresh_stamps()
            self._populate_manage_panel()
            self._set_status(f"Saldo: {balance} sats")

        # The auto-check on selection must not nag with an error dialog when
        # there's no connection - just leave a quiet note in the status bar.
        on_error = None
        if silent:
            def on_error(_exc):
                self._set_status("No se pudo consultar el saldo automaticamente (sin conexion?).")

        self._run_async(work, done, label="Consultando saldo...", on_error=on_error)

    def _recover_funds(self) -> None:
        record = self._current_stamp()
        if not record or self._busy:
            return
        destination = simpledialog.askstring(
            APP_TITLE,
            "Direccion BCH de destino (a donde recuperar los fondos):",
            parent=self,
        )
        if not destination:
            return
        destination = destination.strip()
        if not wallet.is_valid_wif(record.wif):
            messagebox.showerror(APP_TITLE, "La clave de esta estampa no es valida.")
            return
        if not messagebox.askyesno(APP_TITLE, f"Barrer todos los fondos de esta estampa hacia:\n{destination}?"):
            return
        stamp_id = record.id
        wif = record.wif

        def work():
            return wallet.sweep_to(wif, destination)

        def done(txid):
            self.storage.update_stamp_status(stamp_id, STATUS_RECOVERED, balance_sats=0)
            self._refresh_stamps()
            self._populate_manage_panel()
            self._to_clipboard(txid)
            messagebox.showinfo(APP_TITLE, f"Fondos enviados.\nTXID (copiado):\n{txid}")
            self._set_status("Fondos recuperados.")

        self._run_async(work, done, label="Enviando transaccion...")

    def _delete_stamp(self) -> None:
        record = self._current_stamp()
        if not record:
            return
        warn = "Eliminar esta estampa?"
        if record.balance_sats > 0:
            warn = "Esta estampa tiene saldo. Si la borras sin la clave perderas los fondos.\n\nEliminar igual?"
        if not messagebox.askyesno(APP_TITLE, warn):
            return
        self.storage.delete_stamp(record.id)
        self.selected_stamp_id = None
        self._refresh_stamps()
        self._clear_manage_panel()
        self._set_status("Estampa eliminada.")

    # -- helpers ---------------------------------------------------------------

    def _run_async(self, work, on_done, label: str = "", on_error=None) -> None:
        self._busy = True
        if label:
            self._set_status(label)

        def runner():
            try:
                result = work()
            except Exception as exc:
                self.after(0, lambda: self._async_error(exc, on_error))
                return
            self.after(0, lambda: self._async_finish(result, on_done))

        threading.Thread(target=runner, daemon=True).start()

    def _async_finish(self, result, on_done) -> None:
        self._busy = False
        on_done(result)

    def _async_error(self, exc, on_error) -> None:
        self._busy = False
        if on_error:
            on_error(exc)
        else:
            messagebox.showerror(APP_TITLE, f"Hubo un problema:\n{exc}")
            self._set_status("Error.")

    def _to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _open_data_folder(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(DATA_DIR)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(DATA_DIR)])


def main() -> None:
    app = ThermalStampsApp()
    app.mainloop()


if __name__ == "__main__":
    main()
