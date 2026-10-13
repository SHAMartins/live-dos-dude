"""EXPERIMENTO (não faz parte do app): vídeo H.264 já codificado -> LiveKit pelo modo PRE_ENCODED.

Uso: python pass_through_codificado.py <x264|mf> <segundos>   (precisa do ffmpeg no PATH e do LiveKit local)
Resultados e limitações: docs/codificacao-na-gpu.md
"""
import asyncio, ctypes, subprocess, sys, threading, time
from ctypes import wintypes as wt
from livekit import api, rtc
from livekit.rtc._ffi_client import FfiClient, FfiHandle
from livekit.rtc._proto import ffi_pb2 as pf, video_frame_pb2 as pv

class FonteCodificada(rtc.VideoSource):
    def __init__(self, w, h):  # não chama o __init__ da base: cria a fonte do tipo ENCODED
        req = pf.FfiRequest()
        req.new_video_source.type = pv.VideoSourceType.VIDEO_SOURCE_ENCODED
        req.new_video_source.resolution.width, req.new_video_source.resolution.height = w, h
        req.new_video_source.is_screencast = True
        resp = FfiClient.instance.request(req)
        self._info = resp.new_video_source.source
        self._ffi_handle = FfiHandle(self._info.handle.id)
        self.w, self.h = w, h
    def enviar(self, au: bytes, chave: bool, ts_us: int) -> bool:
        buf = ctypes.create_string_buffer(au, len(au))
        req = pf.FfiRequest(); c = req.capture_encoded_video_frame
        c.source_handle = self._ffi_handle.handle
        c.buffer.data_ptr, c.buffer.data_len = ctypes.addressof(buf), len(au)
        c.codec = pv.VideoCodec.H264
        c.frame_type = pv.EncodedFrameType.ENCODED_FRAME_KEY if chave else pv.EncodedFrameType.ENCODED_FRAME_DELTA
        c.width, c.height, c.timestamp_us = self.w, self.h, ts_us
        return FfiClient.instance.request(req).capture_encoded_video_frame.accepted
    def feedback(self):
        req = pf.FfiRequest(); req.take_encoded_video_source_feedback.source_handle = self._ffi_handle.handle
        return FfiClient.instance.request(req).take_encoded_video_source_feedback

def tempo_cpu(proc):
    k = ctypes.WinDLL("kernel32", use_last_error=True); k.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    c, e, kt, ut = (wt.FILETIME() for _ in range(4))
    k.GetProcessTimes(int(proc._handle), ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
    f = lambda x: ((x.dwHighDateTime << 32) | x.dwLowDateTime) / 1e7
    return f(kt) + f(ut)

def unidades_de_acesso(leitura):
    """Gera (bytes, é_chave) por unidade de acesso H.264 em Annex-B, separando por AUD (NAL tipo 9)."""
    buf = bytearray(); atual = None
    while True:
        dados = leitura(65536)
        if not dados: break
        buf += dados
        while True:
            # procura o próximo início de AUD depois do primeiro byte
            i = buf.find(b"\x00\x00\x00\x01\x09", 1)
            if i < 0: break
            au, buf = bytes(buf[:i]), buf[i:]
            yield au, any((au[k + 4] & 0x1F) == 5 for k in range(len(au) - 5) if au[k:k + 4] == b"\x00\x00\x00\x01")

async def main():
    modo, segundos = sys.argv[1], int(sys.argv[2])
    w, h, fps, kbps = 1280, 720, 60, 4000
    cod = {"x264": f"-c:v libx264 -preset ultrafast -tune zerolatency -x264-params repeat-headers=1:aud=1:keyint={fps * 2}",
           "mf": "-c:v h264_mf -hw_encoding 1 -rate_control cbr", "mfgpu": "-c:v h264_mf -hw_encoding 1 -rate_control cbr"}[modo]
    entrada = f'ddagrab=0:framerate={fps}'
    vf = (f"scale_d3d11={w}:{h}:format=nv12,hwdownload,format=nv12" if modo == "mfgpu"
          else f"hwdownload,format=bgra,scale={w}:{h}:flags=fast_bilinear,format=nv12")
    cmd = (f'ffmpeg -hide_banner -loglevel error -f lavfi -i {entrada} -vf "{vf}" '
           f'{cod} -b:v {kbps}k -maxrate {kbps}k -bufsize {kbps}k -bsf:v h264_metadata=aud=insert -f h264 -')
    token = (api.AccessToken("devkey", "devsecret_local_apenas_para_testes_0123456789").with_identity("pc-codificado")
             .with_grants(api.VideoGrants(room_join=True, room="codificado", can_publish=True)).to_jwt())
    sala = rtc.Room(); await sala.connect("ws://localhost:7880", token)
    fonte = FonteCodificada(w, h)
    track = rtc.LocalVideoTrack.create_video_track("tela", fonte)
    await sala.local_participant.publish_track(track, rtc.TrackPublishOptions(
        source=rtc.TrackSource.SOURCE_SCREENSHARE, video_codec=rtc.VideoCodec.H264, simulcast=False,
        video_encoder="ENCODER_BACKEND_PRE_ENCODED", video_encoding=rtc.VideoEncoding(max_framerate=fps, max_bitrate=kbps * 1000)))
    print("publicado (pass-through)", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    t0, c0 = time.perf_counter(), time.process_time(); aus = aceitos = chaves = bytes_ = 0; ff0 = None
    erro = []
    threading.Thread(target=lambda: erro.append(proc.stderr.read()), daemon=True).start()
    try:
        for au, chave in unidades_de_acesso(proc.stdout.read1 if hasattr(proc.stdout, "read1") else proc.stdout.read):
            if ff0 is None: ff0 = tempo_cpu(proc); t0, c0 = time.perf_counter(), time.process_time(); aus = aceitos = chaves = bytes_ = 0
            aus += 1; chaves += chave; bytes_ += len(au)
            aceitos += fonte.enviar(au, chave, int((time.perf_counter() - t0) * 1e6))
            if time.perf_counter() - t0 >= segundos: break
    finally:
        dt = time.perf_counter() - t0; ff = tempo_cpu(proc) - (ff0 or 0); py = time.process_time() - c0
        fb = fonte.feedback()
        proc.kill()
    print(f"modo={modo}: {aus / dt:.1f} fps | aceitos {aceitos}/{aus} | chaves {chaves} | {bytes_ * 8 / dt / 1000:.0f} kbps | "
          f"CPU ffmpeg {ff / dt * 100:.0f}% + python {py / dt * 100:.0f}% = {(ff + py) / dt * 100:.0f}% de 1 núcleo", flush=True)
    print("feedback:", "keyframe_requested=", fb.keyframe_requested, "| rate_control:", str(fb.rate_control).replace("\n", " "), flush=True)
    if erro and erro[0]: print("ffmpeg stderr:", erro[0].decode("utf-8", "replace")[:300])
    await sala.disconnect()
asyncio.run(main())
