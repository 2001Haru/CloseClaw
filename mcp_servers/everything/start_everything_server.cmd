@echo off
setlocal

set PKG_VERSION=%1
if "%PKG_VERSION%"=="" set PKG_VERSION=latest

if /I "%PKG_VERSION%"=="latest" (
  set PKG=@modelcontextprotocol/server-everything
) else (
  set PKG=@modelcontextprotocol/server-everything@%PKG_VERSION%
)

REM Use npx so the official Everything server can run without local npm install.
npx -y %PKG%
