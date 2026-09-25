"""WindowsOrganizer: organiza a pasta Downloads em segundo plano, com ícone na bandeja do Windows."""
import ctypes
import logging
import queue
import sys
import tkinter as tk

import pystray
from PIL import Image, ImageDraw

from organizer.config import APP_NAME, LOG_PATH
from organizer.engine import Organizer
from organizer.gui import App

logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _icone() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 14, 60, 56), 6, fill=(240, 180, 40))    # pasta
    d.rounded_rectangle((4, 8, 28, 20), 4, fill=(240, 180, 40))     # aba
    d.line((18, 36, 28, 46, 46, 26), fill=(255, 255, 255), width=6)  # check
    return img


def main() -> None:
    # Só uma instância por vez.
    ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(None, f"O {APP_NAME} já está rodando (veja o ícone perto do relógio).",
                                         APP_NAME, 0x40)
        return

    comandos: queue.Queue = queue.Queue()  # o tkinter só pode ser mexido pela thread principal
    icon: pystray.Icon | None = None

    def notificar(titulo: str, msg: str) -> None:
        if icon is not None:
            try:
                icon.notify(msg, titulo)
            except Exception:
                logging.exception("Falha ao notificar")

    org = Organizer(notify=notificar)

    root = tk.Tk()
    root.withdraw()
    app = App(root, org)

    def alternar_pausa(_icon, _item):
        org.pausado = not org.pausado

    icon = pystray.Icon(APP_NAME, _icone(), APP_NAME, menu=pystray.Menu(
        pystray.MenuItem("Abrir", lambda: comandos.put("abrir"), default=True),
        pystray.MenuItem("Pausar", alternar_pausa, checked=lambda _: org.pausado),
        pystray.MenuItem("Varrer a pasta agora", lambda: org.varrer()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", lambda: comandos.put("sair")),
    ))

    def processar_comandos():
        try:
            while True:
                cmd = comandos.get_nowait()
                if cmd == "abrir":
                    app.mostrar()
                elif cmd == "sair":
                    org.stop()
                    icon.stop()
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(200, processar_comandos)

    org.start()
    icon.run_detached()
    if "--minimizado" not in sys.argv:
        app.mostrar()
    root.after(200, processar_comandos)
    root.mainloop()


if __name__ == "__main__":
    main()
