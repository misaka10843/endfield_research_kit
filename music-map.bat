@echo off
setlocal
rem Static music-map recovery and publication; reuses decoded audio.
rem Optional portable directory: music-map.bat --music-site reports\music_site
if exist "%~dp0endfield_paths.bat" call "%~dp0endfield_paths.bat"
if errorlevel 1 exit /b %errorlevel%
pushd "%~dp0" || exit /b 1
where uv >nul 2>&1
if errorlevel 1 (
  python -m scripts.webui.audio.build_audio --music-map-only %*
) else (
  uv run --no-project python -m scripts.webui.audio.build_audio --music-map-only %*
)
set "MUSIC_MAP_EXIT=%errorlevel%"
popd
endlocal & exit /b %MUSIC_MAP_EXIT%
