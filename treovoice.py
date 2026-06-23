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
