import calendar
import tkinter as tk
from datetime import date
from tkinter import ttk


class CalendarPicker:
    def __init__(self, parent, selected, choose, anchor, minimum):
        self.minimum = date.fromisoformat(minimum)
        self.maximum = None
        self.choose = choose
        self.selected = date.fromisoformat(selected) if selected else None
        initial = self.selected or date.today()
        self.window = tk.Toplevel(parent)
        self.window.title("选择榜单时间")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())
        self.year = tk.IntVar(value=initial.year)
        self.month = tk.IntVar(value=initial.month)
        panel = ttk.Frame(self.window, padding=10)
        panel.pack(fill="both", expand=True)
        header = ttk.Frame(panel)
        header.pack(fill="x", pady=(0, 8))
        ttk.Button(header, text="‹", width=3, command=lambda: self.move(-1)).pack(
            side="left"
        )
        years = ttk.Combobox(
            header,
            textvariable=self.year,
            values=list(
                range(self.minimum.year, max(date.today().year, initial.year) + 1)
            ),
            width=6,
            state="readonly",
        )
        self.years = years
        years.pack(side="left", padx=(6, 2))
        months = ttk.Combobox(
            header,
            textvariable=self.month,
            values=list(range(1, 13)),
            width=4,
            state="readonly",
        )
        months.pack(side="left", padx=(2, 6))
        for box in (years, months):
            box.bind("<<ComboboxSelected>>", lambda event: self.render())
        ttk.Button(header, text="›", width=3, command=lambda: self.move(1)).pack(
            side="right"
        )
        self.days = ttk.Frame(panel)
        self.days.pack(fill="x")
        self.day_buttons = {}
        self.render()
        self.publication = tk.StringVar(value="正在读取 Pixiv 当前发布日期…")
        ttk.Label(panel, textvariable=self.publication).pack(anchor="w", pady=(8, 0))
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="保持最新", command=lambda: self.select("")).pack(
            side="left"
        )
        ttk.Button(actions, text="取消", command=self.close).pack(side="right")
        self.window.update_idletasks()
        x = min(
            anchor.winfo_rootx(),
            max(0, parent.winfo_screenwidth() - self.window.winfo_reqwidth()),
        )
        y = anchor.winfo_rooty() + anchor.winfo_height()
        if y + self.window.winfo_reqheight() > parent.winfo_screenheight():
            y = max(0, anchor.winfo_rooty() - self.window.winfo_reqheight())
        self.window.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.window.grab_set()
        self.window.focus_set()

    def move(self, offset):
        year, month = divmod(self.year.get() * 12 + self.month.get() - 1 + offset, 12)
        target = date(year, month + 1, 1)
        upper = self.maximum or date.today()
        if (
            date(self.minimum.year, self.minimum.month, 1)
            <= target
            <= date(upper.year, upper.month, 1)
        ):
            self.year.set(year)
            self.month.set(month + 1)
            self.render()

    def render(self):
        for child in self.days.winfo_children():
            child.destroy()
        self.day_buttons = {}
        for column, label in enumerate("一二三四五六日"):
            ttk.Label(self.days, text=label, anchor="center").grid(
                row=0, column=column, sticky="ew", pady=(0, 4)
            )
        for row, week in enumerate(
            calendar.monthcalendar(self.year.get(), self.month.get()), 1
        ):
            for column, day in enumerate(week):
                if not day:
                    continue
                value = date(self.year.get(), self.month.get(), day)
                button = ttk.Button(
                    self.days,
                    text=str(day),
                    width=4,
                    command=lambda value=value: self.select(value.isoformat()),
                )
                button.grid(row=row, column=column, padx=1, pady=1)
                if self.maximum is None or value < self.minimum or value > self.maximum:
                    button.state(["disabled"])
                elif value == self.selected:
                    button.state(["pressed"])
                self.day_buttons[day] = button

    def set_publication(self, ending, period):
        self.maximum = date.fromisoformat(ending)
        self.publication.set("当前发布：" + period)
        self.years["values"] = list(range(self.minimum.year, self.maximum.year + 1))
        if (self.year.get(), self.month.get()) > (
            self.maximum.year,
            self.maximum.month,
        ):
            self.year.set(self.maximum.year)
            self.month.set(self.maximum.month)
        self.render()
        self.window.update_idletasks()

    def select(self, value):
        self.close()
        self.choose(value)

    def close(self):
        self.window.grab_release()
        self.window.destroy()
