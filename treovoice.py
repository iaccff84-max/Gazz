import websocket
import json
import threading
import time
import socket
import struct
import os
from colorama import init, Fore, Style
import sys
import subprocess
import glob
from pathlib import Path

init(autoreset=True)

CONFIG_FILE = "voice_config.txt"
TOKEN_FILE_1 = "token.txt"
TOKEN_FILE_2 = "Token.txt"
MUSIC_FOLDER = "music"

class AudioProcessor:
    """Xử lý audio files và convert sang định dạng phù hợp"""
    def __init__(self):
        self.supported_formats = ['.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac']
        self.output_format = 'wav'
        
    def get_music_files(self):
        """Lấy danh sách các file nhạc trong folder"""
        if not os.path.exists(MUSIC_FOLDER):
            os.makedirs(MUSIC_FOLDER)
            return []
        
        music_files = []
        for ext in self.supported_formats:
            music_files.extend(glob.glob(os.path.join(MUSIC_FOLDER, f"*{ext}")))
            music_files.extend(glob.glob(os.path.join(MUSIC_FOLDER, f"*{ext.upper()}")))
        
        return sorted(music_files)
    
    def convert_audio(self, input_file, output_file):
        """Convert audio file sang WAV 48kHz"""
        try:
            cmd = [
                'ffmpeg',
                '-i', input_file,
                '-acodec', 'pcm_s16le',
                '-ar', '48000',
                '-ac', '2',
                '-y',
                output_file
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            return True
        except Exception as e:
            print(f"{Fore.RED}✗ Lỗi convert: {str(e)}{Style.RESET_ALL}")
            return False
    
    def prepare_audio(self, music_file):
        """Chuẩn bị file audio để phát"""
        output_file = f"temp_audio_{int(time.time())}.wav"
        
        if not os.path.exists(music_file):
            print(f"{Fore.RED}✗ File không tồn tại{Style.RESET_ALL}")
            return None
        
        if self.convert_audio(music_file, output_file):
            return output_file
        return None

class DiscordVoiceBot:
    def __init__(self, token: str):
        self.token = token
        self.ws = None
        self.voice_ws = None
        self.running = True
        self.heartbeat_interval = None
        self.heartbeat_thread = None
        self.voice_heartbeat_thread = None
        self.sequence = None
        self.user_id = None
        self.session_id = None
        self.identified = False
        self.voice_connected = False
        
        # Voice gateway info
        self.voice_endpoint = None
        self.voice_token = None
        self.server_id = None
        self.voice_heartbeat_interval = None
        self.voice_ssrc = None
        self.voice_port = None
        self.voice_ip = None
        self.secret_key = None
        
        # UDP socket
        self.udp_socket = None
        self.udp_thread = None
        
        # Parameters
        self.guild_id = None
        self.channel_id = None
        
        # Voice state
        self.self_mute = False
        self.self_deaf = False
        self.self_video = False
        self.self_stream = False
        
        # Voice retry
        self.voice_retry_count = 0
        self.max_voice_retries = 3
        self.reconnect_delay = 10
        
        # Audio
        self.audio_processor = AudioProcessor()
        self.audio_playing = False
        self.audio_thread = None
        
    def on_message(self, ws, message):
        """Xử lý message từ Discord Gateway"""
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            s = data.get('s')
            
            if s:
                self.sequence = s
            
            if op == 10:
                self.heartbeat_interval = data['d']['heartbeat_interval'] / 1000
                
                self.heartbeat_thread = threading.Thread(target=self.send_heartbeat, daemon=True)
                self.heartbeat_thread.start()
                
                self.identify()
                
            elif t == "READY":
                self.user_id = data['d']['user']['id']
                self.session_id = data['d']['session_id']
                self.identified = True
                print(f"{Fore.LIGHTGREEN_EX}✓ Kết nối thành công{Style.RESET_ALL}")
                
                time.sleep(0.2)
                self.voice_state_update(self.guild_id, self.channel_id)
                
            elif t == "VOICE_STATE_UPDATE":
                d = data.get('d', {})
                if d.get('guild_id') == self.guild_id and d.get('channel_id') == self.channel_id:
                    pass
                
            elif t == "VOICE_SERVER_UPDATE":
                self.voice_endpoint = data['d'].get('endpoint')
                self.voice_token = data['d'].get('token')
                self.server_id = data['d'].get('guild_id')
                
                if self.voice_endpoint:
                    time.sleep(0.5)
                    self.voice_retry_count = 0
                    self.connect_voice_gateway()
                
            elif op == 9:
                print(f"{Fore.RED}✗ Token bị ban{Style.RESET_ALL}")
                self.running = False
                
        except Exception as e:
            pass

    def on_error(self, ws, error):
        pass

    def on_close(self, ws, close_status_code, close_msg):
        pass

    def on_open(self, ws):
        pass

    def send_heartbeat(self):
        """Gửi heartbeat tới Gateway"""
        while self.running and self.heartbeat_interval:
            try:
                time.sleep(self.heartbeat_interval - 1)
                if self.ws and self.ws.sock:
                    self.ws.send(json.dumps({"op": 1, "d": self.sequence}))
            except Exception as e:
                break

    def send_voice_heartbeat(self):
        """Gửi heartbeat tới Voice Gateway"""
        while self.running and self.voice_heartbeat_interval and self.voice_ws and self.voice_connected:
            try:
                time.sleep(self.voice_heartbeat_interval / 1000 - 0.1)
                if self.voice_ws and self.voice_ws.sock:
                    self.voice_ws.send(json.dumps({
                        "op": 3,
                        "d": int(time.time() * 1000)
                    }))
            except Exception as e:
                self.voice_connected = False
                break

    def send_udp_heartbeat(self):
        """Gửi UDP keepalive packets"""
        try:
            while self.running and self.udp_socket and self.voice_connected:
                time.sleep(5)
                if self.udp_socket:
                    packet = struct.pack('>I', 0)
                    self.udp_socket.sendto(packet, (self.voice_ip, self.voice_port))
        except Exception as e:
            self.voice_connected = False

    def send_audio_data(self, audio_file):
        """Gửi audio data qua UDP socket"""
        try:
            if not os.path.exists(audio_file):
                print(f"{Fore.RED}✗ File audio không tồn tại{Style.RESET_ALL}")
                return
            
            with open(audio_file, 'rb') as f:
                # Bỏ qua WAV header (44 bytes)
                f.seek(44)
                self.audio_playing = True
                
                frame_size = 3840  # 48kHz * 20ms * 2 bytes * 2 channels
                
                while self.running and self.audio_playing and self.voice_connected:
                    audio_chunk = f.read(frame_size)
                    if not audio_chunk:
                        break
                    
                    try:
                        self.udp_socket.sendto(audio_chunk, (self.voice_ip, self.voice_port))
                        time.sleep(0.02)  # 20ms delay
                    except Exception as e:
                        break
            
            self.audio_playing = False
            print(f"{Fore.LIGHTGREEN_EX}✓ Phát nhạc hoàn thành{Style.RESET_ALL}")
            
            # Clean up temp file
            try:
                os.remove(audio_file)
            except:
                pass
                
        except Exception as e:
            print(f"{Fore.RED}✗ Lỗi phát nhạc: {str(e)}{Style.RESET_ALL}")
            self.audio_playing = False

    def play_music(self, music_file):
        """Phát nhạc"""
        if self.audio_playing:
            print(f"{Fore.YELLOW}⚠ Đang phát nhạc, vui lòng chờ{Style.RESET_ALL}")
            return
        
        if not self.voice_connected:
            print(f"{Fore.YELLOW}⚠ Voice chưa sẵn sàng, chờ 2 giây...{Style.RESET_ALL}")
            time.sleep(2)
            if not self.voice_connected:
                print(f"{Fore.RED}✗ Chưa kết nối voice{Style.RESET_ALL}")
                return
        
        print(f"{Fore.LIGHTBLUE_EX}► Đang chuẩn bị nhạc...{Style.RESET_ALL}")
        
        audio_file = self.audio_processor.prepare_audio(music_file)
        if audio_file:
            print(f"{Fore.LIGHTGREEN_EX}► Bắt đầu phát nhạc: {os.path.basename(music_file)}{Style.RESET_ALL}")
            self.audio_thread = threading.Thread(target=self.send_audio_data, args=(audio_file,), daemon=True)
            self.audio_thread.start()
        else:
            print(f"{Fore.RED}✗ Không thể chuẩn bị file nhạc{Style.RESET_ALL}")

    def identify(self):
        """Gửi IDENTIFY payload - Discord API v10"""
        identify_payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "intents": 513,
                "properties": {
                    "os": "Linux",
                    "browser": "Discord Client",
                    "device": "Discord Client",
                    "system_locale": "vi-VN",
                    "browser_user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "browser_version": "",
                    "os_version": "",
                    "referrer": "",
                    "referring_domain": ""
                },
                "compress": False,
                "large_threshold": 250
            }
        }
        self.ws.send(json.dumps(identify_payload))

    def voice_state_update(self, guild_id: str, channel_id: str):
        """Gửi VOICE_STATE_UPDATE"""
        if not self.identified:
            return
            
        voice_state_payload = {
            "op": 4,
            "d": {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "self_mute": self.self_mute,
                "self_deaf": self.self_deaf,
                "self_video": self.self_video,
                "self_stream": self.self_stream
            }
        }
        self.ws.send(json.dumps(voice_state_payload))

    def fake_stream_create(self):
        """Tạo fake live stream - op=18"""
        if not self.identified:
            return
            
        fake_stream_payload = {
            "op": 18,
            "d": {
                "type": "guild",
                "guild_id": self.guild_id,
                "channel_id": self.channel_id,
                "preferred_region": None
            }
        }
        self.ws.send(json.dumps(fake_stream_payload))
        print(f"{Fore.MAGENTA}► Live stream bắt đầu{Style.RESET_ALL}")

    def toggle_mute(self, mute: bool):
        """Bật/tắt mic"""
        self.self_mute = mute
        self.voice_state_update(self.guild_id, self.channel_id)
        status = "Tắt" if mute else "Bật"
        print(f"{Fore.LIGHTYELLOW_EX}► Mic {status}{Style.RESET_ALL}")

    def toggle_deaf(self, deaf: bool):
        """Bật/tắt loa"""
        self.self_deaf = deaf
        self.voice_state_update(self.guild_id, self.channel_id)
        status = "Tắt" if deaf else "Bật"
        print(f"{Fore.LIGHTGREEN_EX}► Loa {status}{Style.RESET_ALL}")

    def voice_identify(self):
        """Gửi IDENTIFY tới Voice Gateway - Voice v9"""
        identify_payload = {
            "op": 0,
            "d": {
                "server_id": self.server_id,
                "user_id": self.user_id,
                "session_id": self.session_id,
                "token": self.voice_token
            }
        }
        self.voice_ws.send(json.dumps(identify_payload))

    def voice_on_message(self, ws, message):
        """Xử lý message từ Voice Gateway"""
        try:
            data = json.loads(message)
            op = data.get('op')
            
            if op == 8:
                self.voice_heartbeat_interval = data['d']['heartbeat_interval']
                
                self.voice_identify()
                
                self.voice_heartbeat_thread = threading.Thread(target=self.send_voice_heartbeat, daemon=True)
                self.voice_heartbeat_thread.start()
                
            elif op == 2:
                self.voice_ssrc = data['d'].get('ssrc')
                self.voice_ip = data['d'].get('ip')
                self.voice_port = data['d'].get('port')
                
                self.setup_udp_socket()
                
                self.udp_thread = threading.Thread(target=self.send_udp_heartbeat, daemon=True)
                self.udp_thread.start()
                
                self.voice_connected = True
                
                time.sleep(0.2)
                self.fake_stream_create()
                    
            elif op == 4:
                mode = data['d'].get('mode')
                secret_key = data['d'].get('secret_key')
                
                if secret_key:
                    self.secret_key = secret_key
                    print(f"{Fore.LIGHTMAGENTA_EX}✓ Treo voice thành công!{Style.RESET_ALL}")
                    self.voice_connected = True
                
            elif op == 5:
                pass
                
        except json.JSONDecodeError:
            pass
        except Exception as e:
            pass

    def setup_udp_socket(self):
        """Tạo UDP socket cho voice"""
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.bind(("0.0.0.0", 0))
        except Exception as e:
            pass

    def voice_on_error(self, ws, error):
        """Xử lý error voice"""
        error_str = str(error)
        if "Session is no longer valid" in error_str or "4014" in error_str:
            self.voice_connected = False
            if self.voice_retry_count < self.max_voice_retries:
                self.voice_retry_count += 1
                time.sleep(self.reconnect_delay)
                self.connect_voice_gateway()

    def voice_on_close(self, ws, close_status_code, close_msg):
        """Khi voice kết nối đóng"""
        self.voice_connected = False
        if close_status_code != 1000 and self.running:
            if self.voice_retry_count < self.max_voice_retries:
                self.voice_retry_count += 1
                time.sleep(self.reconnect_delay)
                self.connect_voice_gateway()

    def voice_on_open(self, ws):
        """Khi voice kết nối mở"""
        pass

    def connect_voice_gateway(self):
        """Kết nối tới Voice Gateway - Voice v9"""
        if not self.voice_endpoint or not self.voice_token:
            return
        
        try:
            endpoint = self.voice_endpoint
            if ':' in endpoint:
                endpoint = endpoint.split(':')[0]
            
            ws_url = f"wss://{endpoint}/?v=9&encoding=json"
            
            self.voice_ws = websocket.WebSocketApp(
                ws_url,
                on_open=self.voice_on_open,
                on_message=self.voice_on_message,
                on_error=self.voice_on_error,
                on_close=self.voice_on_close
            )
            
            voice_thread = threading.Thread(target=self.voice_ws.run_forever, daemon=True)
            voice_thread.start()
            
        except Exception as e:
            pass

    def connect(self, guild_id: str, channel_id: str):
        """Kết nối tới Discord Gateway - API v10"""
        self.guild_id = guild_id
        self.channel_id = channel_id
        
        try:
            ws_url = "wss://gateway.discord.gg/?v=10&encoding=json"
            
            self.ws = websocket.WebSocketApp(
                ws_url,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            
            self.ws.run_forever()
            
        except Exception as e:
            pass
        finally:
            self.running = False
            if self.ws:
                try:
                    self.ws.close()
                except:
                    pass
            if self.voice_ws:
                try:
                    self.voice_ws.close()
                except:
                    pass
            if self.udp_socket:
                try:
                    self.udp_socket.close()
                except:
                    pass

def load_token_from_file():
    """Tải token từ file token.txt hoặc Token.txt"""
    if os.path.exists(TOKEN_FILE_1):
        with open(TOKEN_FILE_1, 'r') as f:
            token = f.read().strip()
            if token:
                print(f"{Fore.LIGHTGREEN_EX}✓ Token tải từ {TOKEN_FILE_1}{Style.RESET_ALL}")
                return token
    
    if os.path.exists(TOKEN_FILE_2):
        with open(TOKEN_FILE_2, 'r') as f:
            token = f.read().strip()
            if token:
                print(f"{Fore.LIGHTGREEN_EX}✓ Token tải từ {TOKEN_FILE_2}{Style.RESET_ALL}")
                return token
    
    return None

def load_config():
    """Tải cấu hình từ file"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                return config
        except:
            pass
    return None

def save_config(guild_id: str, channel_id: str, self_mute: bool, self_deaf: bool):
    """Lưu cấu hình vào file"""
    config = {
        "guild_id": guild_id,
        "channel_id": channel_id,
        "self_mute": self_mute,
        "self_deaf": self_deaf
    }
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def display_config(guild_id: str, channel_id: str, self_mute: bool, self_deaf: bool):
    """Hiển thị cấu hình đã lưu"""
    print(f"\n{Fore.LIGHTCYAN_EX}{'='*50}")
    print(f"  CẤU HÌNH HIỆN TẠI")
    print(f"{'='*50}")
    print(f"{Fore.LIGHTYELLOW_EX}Guild ID      : {Fore.LIGHTGREEN_EX}{guild_id}")
    print(f"{Fore.LIGHTYELLOW_EX}Channel ID    : {Fore.LIGHTGREEN_EX}{channel_id}")
    print(f"{Fore.LIGHTYELLOW_EX}Mic           : {Fore.LIGHTGREEN_EX}{'Tắt' if self_mute else 'Bật'}")
    print(f"{Fore.LIGHTYELLOW_EX}Loa           : {Fore.LIGHTGREEN_EX}{'Tắt' if self_deaf else 'Bật'}{Style.RESET_ALL}")
    print(f"{Fore.LIGHTCYAN_EX}{'='*50}{Style.RESET_ALL}\n")

def show_music_menu(bot):
    """Hiển thị menu chọn nhạc"""
    music_files = bot.audio_processor.get_music_files()
    
    if not music_files:
        print(f"{Fore.RED}✗ Không tìm thấy file nhạc trong folder '{MUSIC_FOLDER}'{Style.RESET_ALL}")
        print(f"{Fore.LIGHTYELLOW_EX}ℹ Vui lòng thêm file nhạc (.mp3, .wav, .flac, .ogg, .m4a, .aac) vào folder '{MUSIC_FOLDER}'{Style.RESET_ALL}")
        return
    
    print(f"\n{Fore.LIGHTCYAN_EX}{'='*60}")
    print(f"  DANH SÁCH NHẠC")
    print(f"{'='*60}")
    
    for idx, file in enumerate(music_files, 1):
        file_name = os.path.basename(file)
        file_size = os.path.getsize(file) / (1024 * 1024)  # Convert to MB
        print(f"{Fore.LIGHTGREEN_EX}[{idx}]{Fore.LIGHTCYAN_EX} {file_name} {Fore.LIGHTYELLOW_EX}({file_size:.2f} MB)")
    
    print(f"{Fore.LIGHTGREEN_EX}[0]{Fore.LIGHTCYAN_EX} Quay lại")
    print(f"{'='*60}{Style.RESET_ALL}\n")
    
    try:
        choice = input(f"{Fore.LIGHTBLUE_EX}Chọn nhạc (số): {Style.RESET_ALL}").strip()
        choice_num = int(choice)
        
        if choice_num == 0:
            return
        elif 1 <= choice_num <= len(music_files):
            selected_music = music_files[choice_num - 1]
            bot.play_music(selected_music)
        else:
            print(f"{Fore.RED}✗ Lựa chọn không hợp lệ{Style.RESET_ALL}")
    except ValueError:
        print(f"{Fore.RED}✗ Vui lòng nhập số{Style.RESET_ALL}")
    except Exception as e:
        print(f"{Fore.RED}✗ Lỗi: {str(e)}{Style.RESET_ALL}")

def main():
    print(f"{Fore.LIGHTCYAN_EX}╔════════════════════════════════════════════╗")
    print(f"║  {Fore.LIGHTMAGENTA_EX}Discord Voice Bot v6 (Phát Nhạc){Fore.LIGHTCYAN_EX}        ║")
    print(f"║  {Fore.LIGHTGREEN_EX}Gateway v10 + Voice v9 + Live Streaming{Fore.LIGHTCYAN_EX}  ║")
    print(f"╚════════════════════════════════════════════╝{Style.RESET_ALL}\n")
    
    # Load token từ file
    token = load_token_from_file()
    
    if not token:
        print(f"{Fore.LIGHTYELLOW_EX}⚠ Không tìm thấy file token{Style.RESET_ALL}")
        token = input(f"{Fore.CYAN}Nhập Discord Token: {Style.RESET_ALL}").strip()
        if not token:
            print(f"{Fore.RED}✗ Token không được để trống{Style.RESET_ALL}")
            return

    # Load config
    config = load_config()
    guild_id = None
    channel_id = None
    self_mute = False
    self_deaf = False
    
    if config:
        guild_id = config.get('guild_id')
        channel_id = config.get('channel_id')
        self_mute = config.get('self_mute', False)
        self_deaf = config.get('self_deaf', False)
        
        display_config(guild_id, channel_id, self_mute, self_deaf)
        change = input(f"{Fore.CYAN}Thay đổi cấu hình? (c/n): {Style.RESET_ALL}").strip().lower()
        
        if change == 'c':
            guild_id = input(f"{Fore.CYAN}Guild ID: {Style.RESET_ALL}").strip()
            channel_id = input(f"{Fore.CYAN}Channel ID: {Style.RESET_ALL}").strip()
            self_mute = False
            self_deaf = False
    else:
        print(f"{Fore.LIGHTYELLOW_EX}ℹ Không có cấu hình trước đó{Style.RESET_ALL}")
        guild_id = input(f"{Fore.CYAN}Guild ID: {Style.RESET_ALL}").strip()
        channel_id = input(f"{Fore.CYAN}Channel ID: {Style.RESET_ALL}").strip()

    if not guild_id or not channel_id:
        print(f"{Fore.RED}✗ Guild ID hoặc Channel ID không được để trống{Style.RESET_ALL}")
        return

    # Save config
    save_config(guild_id, channel_id, self_mute, self_deaf)

    print(f"\n{Fore.LIGHTGREEN_EX}Đang kết nối...{Style.RESET_ALL}\n")

    bot = DiscordVoiceBot(token)
    bot.self_mute = self_mute
    bot.self_deaf = self_deaf

    # Thread để xử lý bot
    bot_thread = threading.Thread(target=bot.connect, args=(guild_id, channel_id), daemon=True)
    bot_thread.start()

    time.sleep(2)

    # Menu điều khiển
    print(f"\n{Fore.LIGHTCYAN_EX}╔════════════════════════════════════════════╗")
    print(f"║  {Fore.LIGHTYELLOW_EX}MENU ĐIỀU KHIỂN{Fore.LIGHTCYAN_EX}                        ║")
    print(f"║  {Fore.LIGHTGREEN_EX}[1]{Fore.LIGHTCYAN_EX} Tắt Mic    {Fore.LIGHTGREEN_EX}[2]{Fore.LIGHTCYAN_EX} Bật Mic        ║")
    print(f"║  {Fore.LIGHTGREEN_EX}[3]{Fore.LIGHTCYAN_EX} Tắt Loa    {Fore.LIGHTGREEN_EX}[4]{Fore.LIGHTCYAN_EX} Bật Loa        ║")
    print(f"║  {Fore.LIGHTGREEN_EX}[5]{Fore.LIGHTCYAN_EX} Bắt Live   {Fore.LIGHTGREEN_EX}[8]{Fore.LIGHTCYAN_EX} Phát Nhạc      ║")
    print(f"║  {Fore.LIGHTGREEN_EX}[6]{Fore.LIGHTCYAN_EX} Tắt Loa+Mic+Live     {Fore.LIGHTGREEN_EX}[0]{Fore.LIGHTCYAN_EX} Thoát      ║")
    print(f"║  {Fore.LIGHTGREEN_EX}[7]{Fore.LIGHTCYAN_EX} Bật Mic+Loa+Live                    ║")
    print(f"╚════════════════════════════════════════════╝{Style.RESET_ALL}\n")

    try:
        while bot.running:
            try:
                cmd = input(f"{Fore.LIGHTBLUE_EX}Lệnh > {Style.RESET_ALL}").strip()
                
                if cmd == "1":
                    bot.toggle_mute(True)
                    save_config(guild_id, channel_id, True, bot.self_deaf)
                elif cmd == "2":
                    bot.toggle_mute(False)
                    save_config(guild_id, channel_id, False, bot.self_deaf)
                elif cmd == "3":
                    bot.toggle_deaf(True)
                    save_config(guild_id, channel_id, bot.self_mute, True)
                elif cmd == "4":
                    bot.toggle_deaf(False)
                    save_config(guild_id, channel_id, bot.self_mute, False)
                elif cmd == "5":
                    bot.fake_stream_create()
                elif cmd == "6":
                    # Tắt Loa + Mic + Bắt Live
                    bot.toggle_mute(True)
                    time.sleep(0.1)
                    bot.toggle_deaf(True)
                    time.sleep(0.1)
                    bot.fake_stream_create()
                    print(f"{Fore.LIGHTCYAN_EX}► Tắt Loa + Mic + Bắt Live{Style.RESET_ALL}")
                    save_config(guild_id, channel_id, True, True)
                elif cmd == "7":
                    # Bật Mic + Loa + Bắt Live
                    bot.toggle_mute(False)
                    time.sleep(0.1)
                    bot.toggle_deaf(False)
                    time.sleep(0.1)
                    bot.fake_stream_create()
                    print(f"{Fore.LIGHTGREEN_EX}► Bật Mic + Loa + Bắt Live{Style.RESET_ALL}")
                    save_config(guild_id, channel_id, False, False)
                elif cmd == "8":
                    show_music_menu(bot)
                elif cmd == "0":
                    print(f"{Fore.YELLOW}⚠ Đang thoát...{Style.RESET_ALL}")
                    bot.running = False
                    break
                else:
                    print(f"{Fore.RED}✗ Lệnh không hợp lệ{Style.RESET_ALL}")
            except EOFError:
                break
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}⚠ Chương trình đã đóng.{Style.RESET_ALL}")
        bot.running = False

if __name__ == "__main__":
    main()
