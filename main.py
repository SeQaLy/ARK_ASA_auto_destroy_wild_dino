import socket
import struct
import time
import datetime

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

    # 指定時間毎にコマンドを実行するメソッド
    def execute_command_periodically(self, command, interval_seconds, notify_time=None, notify_message=None):
        if interval_seconds <= 0:
            raise ValueError("interval_seconds は 1 以上を指定してください")

        notify_offsets = self._parse_time_schedule(notify_time) if notify_time is not None else []

        while True:
            run_started_at = datetime.datetime.now()
            try:
                response = self.execute_command(command)
                self.execute_command(f"ServerChat (System){response}")
                print(f"{run_started_at}: {response}")
            except Exception as e:
                print(f"エラー: {e}")

            next_run_at = run_started_at + datetime.timedelta(seconds=interval_seconds)
            notify_candidates = []
            for hour, minute in notify_offsets:
                candidate = next_run_at - datetime.timedelta(hours=hour, minutes=minute)
                if candidate > run_started_at:
                    notify_candidates.append(candidate)

            sent_notifications = set()

            while True:
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
                        self.execute_command(f"ServerChat (System){message}")

                if now >= next_run_at:
                    break

                time.sleep(1)

    # 指定時刻にコマンドを実行するメソッド
    def execute_command_at_time(self, command, *target_time, notify_time=None, notify_message=None):
        schedule = self._parse_time_schedule(*target_time)
        notify_offsets = self._parse_time_schedule(notify_time) if notify_time is not None else []

        while True:
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

            while True:
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
                        self.execute_command(f"ServerChat (System){message}")

                if now >= run_at:
                    try:
                        response = self.execute_command(command)
                        self.execute_command(f"ServerChat (System){response}")
                        print(f"{now}: {response}")
                    except Exception as e:
                        print(f"エラー: {e}")
                    break

                time.sleep(1)

# 時間列挙クラス
class ConstTime:
    MINUTE = 60
    HOUR = 3600
    DAY = 24 * HOUR

class ConstCommand:
    DESTROY_WILD_DINOS = "DestroyWildDinos"


########################### 
HOST = "127.0.0.1"
PORT = 27020
PASSWORD = "password"
# コマンドを実行する時刻を "H:MM" 形式で指定。例えば "0:00" は毎日0時、"6:00" は毎日6時に実行。
TARGET_TIMES = ["00:00", "06:00", "12:00", "18:00"]
# 通知時間を "H:MM" 形式で指定。例えば "1:00" はコマンド実行の1時間前、"0:30" は30分前、"0:10" は10分前に通知。
NOTIFY_TIMES = ["1:00", "0:30", "0:10"]
# 定期実行の例で、コマンドを実行する間隔を秒数で指定。
PERIODIC_INTERVAL_SECONDS = ConstTime.HOUR * 6  
# 通知メッセージ(文字化けにより日本語NG)
NOTIFY_MESSAGE = "{remaining_minutes} minutes until {command} command is executed"
###########################

if __name__ == "__main__":
    
    rcon = RconHandler(HOST, PORT, PASSWORD)
    try:
        # 指定時刻にコマンドを実行する場合
        # rcon.execute_command_at_time(
        #     ConstCommand.DESTROY_WILD_DINOS,
        #     TARGET_TIMES,
        #     notify_time=NOTIFY_TIMES,
        #     notify_message=NOTIFY_MESSAGE
        # )

        # 起動してから一定時間ごとにコマンドを実行する場合
        rcon.execute_command_periodically(
            ConstCommand.DESTROY_WILD_DINOS,
            interval_seconds=PERIODIC_INTERVAL_SECONDS,
            notify_time=NOTIFY_TIMES,
            notify_message=NOTIFY_MESSAGE
        )
        # rcon.execute_command_periodically(ConstCommand.DESTROY_WILD_DINOS, ConstTime.MINUTE_1)
    except Exception as e:
        print(f"エラー: {e}")