#!/usr/bin/env bash
# minimal ffmpeg：仅 WAV/AIFF 解复用 + PCM 解码 + FLAC 编码（供 float WAV 回退）
# 注意：
#   - ffmpeg 程序硬依赖 avfilter（configure 中 ffmpeg_deps）
#   - float WAV → FLAC 需要 swresample / aresample 做采样格式转换
# 在 MSYS2 MINGW64/UCRT64 环境中 source 本文件后执行 ./configure

FFMPEG_MINIMAL_CONFIGURE_FLAGS=(
  --disable-debug
  --disable-doc
  --disable-network
  --disable-autodetect
  --disable-everything
  --disable-ffplay
  --disable-ffprobe
  --disable-avdevice
  --disable-swscale
  --disable-postproc
  --enable-ffmpeg
  --enable-avfilter
  --enable-swresample
  --enable-small
  --enable-static
  --disable-shared
  --enable-avcodec
  --enable-avformat
  --enable-avutil
  --enable-decoder=pcm_s16le,pcm_s24le,pcm_s32le,pcm_s16be,pcm_s24be,pcm_s32be,pcm_f32le,pcm_f64le,pcm_f32be,pcm_f64be,pcm_u8,pcm_u16le,pcm_u16be,pcm_u24le,pcm_u24be,pcm_alaw,pcm_mulaw
  --enable-encoder=flac
  --enable-demuxer=wav,aiff
  --enable-muxer=flac
  --enable-protocol=file
  --enable-filter=aformat,anull,aresample,atrim,crop,format,hflip,null,rotate,transpose,trim,vflip
  --extra-cflags=-Os
  --extra-ldexeflags=-static
)
