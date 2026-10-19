"""Run the Polar H10 + microphone application."""
import tkinter as tk
from app import PolarH10LSLApp


def main() -> None:
    root = tk.Tk()
    PolarH10LSLApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
