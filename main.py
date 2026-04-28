import socket
import struct
import time
import datetime
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

class SocketServer:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.sock = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect((self.host, self.port))

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def send_packet(self, packet_id, packet_type, body):
        body_bytes = body.encode("utf-8")
        # RCONパケットの構造はこうらしい
        #+----------+------------+--------------+---------------+-----------+
        #| Size(int) | ID(4Byte) | Type(4Byte) | Body(NByte) | NULL文字     |
        #+----------+------------+--------------+---------------+-----------+
        size = len(body_bytes) + 10  # id(4) + type(4) + body + 2 null bytes
        packet = struct.pack("<iii", size, packet_id, packet_type)
        packet += body_bytes + b"\x00\x00"
        self.sock.sendall(packet)

    def recv_exact(self, nbytes):
        data = b""
        while len(data) < nbytes:
            chunk = self.sock.recv(nbytes - len(data))
            if not chunk:
                raise ConnectionError("サーバーによって接続が閉じられました")
            data += chunk
        return data

    def recv_packet(self):
        size_data = self.recv_exact(4)
        size = struct.unpack("<i", size_data)[0]
        data = self.recv_exact(size)
        packet_id, packet_type = struct.unpack("<ii", data[:8])
        body = data[8:-2].decode("utf-8")
        return packet_id, packet_type, body

class RconHandler:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password

    def rcon_command(self, host, port, password, command):
        server = SocketServer(host, port, password)
        server.connect()

        try:
            auth_id = 1
            server.send_packet(auth_id, 3, password)
            auth_packet_id, _, _ = server.recv_packet()
            if auth_packet_id == -1:
                raise PermissionError("RCON認証失敗(パスワードが間違っています)")

            cmd_id = 2
            server.send_packet(cmd_id, 2, command)
            response_packet_id, _, response = server.recv_packet()

            if response_packet_id != cmd_id:
                raise RuntimeError("パケットID不一致")

            if response.strip() == "Server received, But no response!!":
                return "コマンド受信成功(サーバーからのメッセージ返却無し)"

            return response
        finally:
            server.close()

    # コマンド実行用のラップメソッド
    def execute_command(self, command):
        return self.rcon_command(self.host, self.port, self.password, command)

    def _parse_time_schedule(self, *time_args):
        if not time_args:
            raise ValueError("時刻は1件以上指定してください")

        if len(time_args) == 1 and isinstance(time_args[0], (list, tuple)):
            time_args = tuple(time_args[0])

        schedule = []

        if all(isinstance(x, int) for x in time_args):
            if len(time_args) % 2 != 0:
                raise ValueError("数値指定は hour, minute のペアで指定してください")
            for i in range(0, len(time_args), 2):
                schedule.append((time_args[i], time_args[i + 1]))
        else:
            for t in time_args:
                if isinstance(t, str):
                    try:
                        hour_str, minute_str = t.split(":", 1)
                        schedule.append((int(hour_str), int(minute_str)))
                    except (ValueError, TypeError):
                        raise ValueError("文字列指定は 'H:MM' 形式で指定してください")
                elif isinstance(t, (tuple, list)) and len(t) == 2:
                    schedule.append((int(t[0]), int(t[1])))
                else:
                    raise ValueError("時刻は 'H:MM'、(hour, minute)、または hour, minute の連続指定にしてください")

        for hour, minute in schedule:
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"時刻が不正です: {hour}:{minute}")

        return schedule

    # 指定時間毎にコマンドを実行するメソッド（stop_event で停止可能）
    def execute_command_periodically(self, command, interval_seconds, notify_time=None, notify_message=None, stop_event=None, log_callback=None):
        if interval_seconds <= 0:
            raise ValueError("interval_seconds は 1 以上を指定してください")

        notify_offsets = self._parse_time_schedule(notify_time) if notify_time is not None else []

        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        while not (stop_event and stop_event.is_set()):
            run_started_at = datetime.datetime.now()
            try:
                response = self.execute_command(command)
                self.execute_command(f"ServerChat (System){response}")
                log(f"{run_started_at}: {response}")
            except Exception as e:
                log(f"エラー: {e}")

            next_run_at = run_started_at + datetime.timedelta(seconds=interval_seconds)
            notify_candidates = []
            for hour, minute in notify_offsets:
                candidate = next_run_at - datetime.timedelta(hours=hour, minutes=minute)
                if candidate > run_started_at:
                    notify_candidates.append(candidate)

            sent_notifications = set()

            while not (stop_event and stop_event.is_set()):
                now = datetime.datetime.now()

                for notify_at in notify_candidates:
                    if notify_at <= now and notify_at not in sent_notifications:
                        sent_notifications.add(notify_at)
                        remaining_minutes = int((next_run_at - notify_at).total_seconds() // 60)
                        if notify_message:
                            message = notify_message.format(
                                remaining_minutes=remaining_minutes,
                                command=command,
                            )
                        else:
                            message = f"{remaining_minutes} minutes until {command} command is executed"
                        try:
                            self.execute_command(f"ServerChat (System){message}")
                            log(f"通知送信: {message}")
                        except Exception as e:
                            log(f"通知エラー: {e}")

                if now >= next_run_at:
                    break

                time.sleep(1)

    # 指定時刻にコマンドを実行するメソッド（stop_event で停止可能）
    def execute_command_at_time(self, command, *target_time, notify_time=None, notify_message=None, stop_event=None, log_callback=None):
        schedule = self._parse_time_schedule(*target_time)
        notify_offsets = self._parse_time_schedule(notify_time) if notify_time is not None else []

        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        while not (stop_event and stop_event.is_set()):
            now = datetime.datetime.now()
            run_candidates = []
            for hour, minute in schedule:
                candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate <= now:
                    candidate += datetime.timedelta(days=1)
                run_candidates.append(candidate)

            run_at = min(run_candidates)

            notify_candidates = []
            for hour, minute in notify_offsets:
                candidate = run_at - datetime.timedelta(hours=hour, minutes=minute)
                if candidate > now:
                    notify_candidates.append(candidate)

            sent_notifications = set()

            while not (stop_event and stop_event.is_set()):
                now = datetime.datetime.now()

                for notify_at in notify_candidates:
                    if notify_at <= now and notify_at not in sent_notifications:
                        sent_notifications.add(notify_at)
                        remaining_minutes = int((run_at - notify_at).total_seconds() // 60)
                        if notify_message:
                            message = notify_message.format(
                                remaining_minutes=remaining_minutes,
                                command=command,
                            )
                        else:
                            message = f"{remaining_minutes} minutes until {command} command is executed"
                        try:
                            self.execute_command(f"ServerChat (System){message}")
                            log(f"通知送信: {message}")
                        except Exception as e:
                            log(f"通知エラー: {e}")

                if now >= run_at:
                    try:
                        response = self.execute_command(command)
                        self.execute_command(f"ServerChat (System){response}")
                        log(f"{now}: {response}")
                    except Exception as e:
                        log(f"エラー: {e}")
                    break

                time.sleep(1)

# 時間列挙クラス
class ConstTime:
    MINUTE = 60
    HOUR = 3600
    DAY = 24 * HOUR

class ConstCommand:
    DESTROY_WILD_DINOS = "DestroyWildDinos"
    SHUTDOWN_SERVER = "ShutdownServer"


########################### 
# デフォルト値（GUIの初期値として使用）
HOST = "127.0.0.1"
PORT = 27020
PASSWORD = "mypassword"
###########################


# ===== GUI =====

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ARK RCON Manager")
        self.resizable(False, False)

        self._stop_event = None
        self._worker_thread = None

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # ── 接続設定 ──────────────────────────────────────────
        conn_frame = ttk.LabelFrame(self, text="接続設定")
        conn_frame.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Label(conn_frame, text="Host:").grid(row=0, column=0, sticky="e", **pad)
        self._host = ttk.Entry(conn_frame, width=20)
        self._host.insert(0, str(HOST))
        self._host.grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(conn_frame, text="Port:").grid(row=0, column=2, sticky="e", **pad)
        self._port = ttk.Entry(conn_frame, width=8)
        self._port.insert(0, str(PORT))
        self._port.grid(row=0, column=3, sticky="w", **pad)

        ttk.Label(conn_frame, text="Password:").grid(row=0, column=4, sticky="e", **pad)
        self._password = ttk.Entry(conn_frame, width=20, show="*")
        self._password.insert(0, PASSWORD)
        self._password.grid(row=0, column=5, sticky="w", **pad)

        # ── コマンド送信（即時） ──────────────────────────────
        cmd_frame = ttk.LabelFrame(self, text="コマンド送信（即時）")
        cmd_frame.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Label(cmd_frame, text="コマンド:").grid(row=0, column=0, sticky="e", **pad)
        self._single_cmd = ttk.Combobox(cmd_frame, width=30,
            values=[ConstCommand.DESTROY_WILD_DINOS, ConstCommand.SHUTDOWN_SERVER])
        self._single_cmd.set(ConstCommand.DESTROY_WILD_DINOS)
        self._single_cmd.grid(row=0, column=1, sticky="w", **pad)

        ttk.Button(cmd_frame, text="実行", command=self._run_single_command).grid(
            row=0, column=2, **pad)

        # ── スケジュール設定 ──────────────────────────────────
        sched_frame = ttk.LabelFrame(self, text="スケジュール実行")
        sched_frame.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)

        # 実行モード
        ttk.Label(sched_frame, text="実行モード:").grid(row=0, column=0, sticky="e", **pad)
        self._mode = tk.StringVar(value="periodic")
        ttk.Radiobutton(sched_frame, text="定期実行（間隔）", variable=self._mode,
                        value="periodic", command=self._on_mode_change).grid(
            row=0, column=1, sticky="w", **pad)
        ttk.Radiobutton(sched_frame, text="時刻指定実行", variable=self._mode,
                        value="at_time", command=self._on_mode_change).grid(
            row=0, column=2, sticky="w", **pad)

        # コマンド
        ttk.Label(sched_frame, text="コマンド:").grid(row=1, column=0, sticky="e", **pad)
        self._sched_cmd = ttk.Combobox(sched_frame, width=30,
            values=[ConstCommand.DESTROY_WILD_DINOS, ConstCommand.SHUTDOWN_SERVER])
        self._sched_cmd.set(ConstCommand.DESTROY_WILD_DINOS)
        self._sched_cmd.grid(row=1, column=1, columnspan=2, sticky="w", **pad)

        # 定期実行：間隔（秒）
        self._interval_label = ttk.Label(sched_frame, text="間隔（秒）:")
        self._interval_label.grid(row=2, column=0, sticky="e", **pad)
        self._interval = ttk.Entry(sched_frame, width=12)
        self._interval.insert(0, str(ConstTime.HOUR * 6))
        self._interval.grid(row=2, column=1, sticky="w", **pad)

        # 時刻指定：実行時刻
        self._times_label = ttk.Label(sched_frame, text="実行時刻 (カンマ区切り H:MM):")
        self._times_label.grid(row=3, column=0, sticky="e", **pad)
        self._target_times = ttk.Entry(sched_frame, width=35)
        self._target_times.insert(0, "00:00, 06:00, 12:00, 18:00")
        self._target_times.grid(row=3, column=1, columnspan=2, sticky="w", **pad)

        # 通知時間
        ttk.Label(sched_frame, text="通知時間 (カンマ区切り H:MM):").grid(
            row=4, column=0, sticky="e", **pad)
        self._notify_times = ttk.Entry(sched_frame, width=35)
        self._notify_times.insert(0, "1:00, 0:30, 0:10")
        self._notify_times.grid(row=4, column=1, columnspan=2, sticky="w", **pad)

        # 通知メッセージ
        ttk.Label(sched_frame, text="通知メッセージ:").grid(row=5, column=0, sticky="e", **pad)
        self._notify_msg = ttk.Entry(sched_frame, width=55)
        self._notify_msg.insert(0, "{remaining_minutes} minutes until {command} command is executed")
        self._notify_msg.grid(row=5, column=1, columnspan=2, sticky="w", **pad)

        # 開始/停止ボタン
        btn_frame = ttk.Frame(sched_frame)
        btn_frame.grid(row=6, column=0, columnspan=3, pady=(4, 8))
        self._start_btn = ttk.Button(btn_frame, text="開始", command=self._start_schedule)
        self._start_btn.grid(row=0, column=0, padx=8)
        self._stop_btn = ttk.Button(btn_frame, text="停止", command=self._stop_schedule, state="disabled")
        self._stop_btn.grid(row=0, column=1, padx=8)

        # ── ログ ─────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="ログ")
        log_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", **pad)
        self._log = scrolledtext.ScrolledText(log_frame, width=80, height=14,
                                              state="disabled", font=("Consolas", 9))
        self._log.pack(fill="both", expand=True, padx=4, pady=4)

        ttk.Button(self, text="ログをクリア", command=self._clear_log).grid(
            row=4, column=0, sticky="w", **pad)

        self._on_mode_change()

    # ── ヘルパー ─────────────────────────────────────────────

    def _on_mode_change(self):
        mode = self._mode.get()
        if mode == "periodic":
            self._interval.config(state="normal")
            self._target_times.config(state="disabled")
        else:
            self._interval.config(state="disabled")
            self._target_times.config(state="normal")

    def _log_write(self, msg):
        """スレッドセーフなログ書き込み（after 経由で UI スレッドに投げる）"""
        self.after(0, self._log_append, msg)

    def _log_append(self, msg):
        self._log.config(state="normal")
        self._log.insert(tk.END, f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        self._log.see(tk.END)
        self._log.config(state="disabled")

    def _clear_log(self):
        self._log.config(state="normal")
        self._log.delete("1.0", tk.END)
        self._log.config(state="disabled")

    def _get_rcon(self):
        host = self._host.get().strip()
        port_str = self._port.get().strip()
        password = self._password.get()
        if not host or not port_str:
            raise ValueError("Host と Port を入力してください")
        return RconHandler(host, int(port_str), password)

    def _parse_time_list(self, text):
        """カンマ区切り文字列を ["H:MM", ...] リストに変換"""
        return [t.strip() for t in text.split(",") if t.strip()]

    # ── 即時コマンド ─────────────────────────────────────────

    def _run_single_command(self):
        command = self._single_cmd.get().strip()
        if not command:
            messagebox.showwarning("警告", "コマンドを入力してください")
            return

        def task():
            try:
                rcon = self._get_rcon()
                self._log_write(f"実行中: {command}")
                response = rcon.execute_command(command)
                self._log_write(f"結果: {response}")
            except Exception as e:
                self._log_write(f"エラー: {e}")

        threading.Thread(target=task, daemon=True).start()

    # ── スケジュール実行 ──────────────────────────────────────

    def _start_schedule(self):
        command = self._sched_cmd.get().strip()
        if not command:
            messagebox.showwarning("警告", "コマンドを入力してください")
            return

        notify_times_raw = self._parse_time_list(self._notify_times.get())
        notify_times = notify_times_raw if notify_times_raw else None
        notify_msg = self._notify_msg.get().strip() or None

        try:
            rcon = self._get_rcon()
        except Exception as e:
            messagebox.showerror("エラー", str(e))
            return

        self._stop_event = threading.Event()
        mode = self._mode.get()

        if mode == "periodic":
            try:
                interval = int(self._interval.get().strip())
            except ValueError:
                messagebox.showerror("エラー", "間隔は整数（秒）で入力してください")
                return

            def task():
                try:
                    self._log_write(f"定期実行開始: {command}  間隔: {interval}秒")
                    rcon.execute_command_periodically(
                        command,
                        interval_seconds=interval,
                        notify_time=notify_times,
                        notify_message=notify_msg,
                        stop_event=self._stop_event,
                        log_callback=self._log_write,
                    )
                except Exception as e:
                    self._log_write(f"スケジュールエラー: {e}")
                finally:
                    self.after(0, self._on_schedule_stopped)

        else:  # at_time
            target_times = self._parse_time_list(self._target_times.get())
            if not target_times:
                messagebox.showwarning("警告", "実行時刻を入力してください")
                return

            def task():
                try:
                    self._log_write(f"時刻指定実行開始: {command}  時刻: {target_times}")
                    rcon.execute_command_at_time(
                        command,
                        target_times,
                        notify_time=notify_times,
                        notify_message=notify_msg,
                        stop_event=self._stop_event,
                        log_callback=self._log_write,
                    )
                except Exception as e:
                    self._log_write(f"スケジュールエラー: {e}")
                finally:
                    self.after(0, self._on_schedule_stopped)

        self._worker_thread = threading.Thread(target=task, daemon=True)
        self._worker_thread.start()

        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")

    def _stop_schedule(self):
        if self._stop_event:
            self._stop_event.set()
            self._log_write("停止シグナル送信済み。最大1秒以内に停止します...")

    def _on_schedule_stopped(self):
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._log_write("スケジュール停止しました")


if __name__ == "__main__":
    app = App()
    app.mainloop()