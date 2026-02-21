@echo off
setlocal enabledelayedexpansion

set ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk
set BUILD_TOOLS=%ANDROID_HOME%\build-tools\34.0.0
set PLATFORM=%ANDROID_HOME%\platforms\android-34
set BUILD_DIR=%~dp0build

echo === Building Scrcpy Companion ===

REM Clean and create directories
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
mkdir "%BUILD_DIR%"
mkdir "%BUILD_DIR%\gen"
mkdir "%BUILD_DIR%\classes"
mkdir "%BUILD_DIR%\dex"
mkdir "%BUILD_DIR%\res\values"
mkdir "%BUILD_DIR%\res\drawable"
mkdir "%BUILD_DIR%\res\layout"

REM Copy resources
copy /y "%~dp0app\src\main\res\values\strings.xml" "%BUILD_DIR%\res\values\" >nul
copy /y "%~dp0app\src\main\res\drawable\ic_tile.xml" "%BUILD_DIR%\res\drawable\" >nul
copy /y "%~dp0app\src\main\res\layout\activity_main.xml" "%BUILD_DIR%\res\layout\" >nul

REM Create manifest with Activity and TileService
echo ^<?xml version="1.0" encoding="utf-8"?^>^<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.genymobile.scrcpy.companion"^>^<uses-sdk android:minSdkVersion="24" android:targetSdkVersion="34"/^>^<uses-permission android:name="android.permission.INTERNET" /^>^<uses-permission android:name="android.permission.BIND_QUICK_SETTINGS_TILE" /^>^<application android:label="@string/app_name" android:icon="@drawable/ic_tile"^>^<activity android:name=".MainActivity" android:label="@string/app_name" android:exported="true"^>^<intent-filter^>^<action android:name="android.intent.action.MAIN" /^>^<category android:name="android.intent.category.LAUNCHER" /^>^</intent-filter^>^</activity^>^<service android:name=".ScrcpyTileService" android:label="@string/tile_label" android:icon="@drawable/ic_tile" android:permission="android.permission.BIND_QUICK_SETTINGS_TILE" android:exported="true"^>^<intent-filter^>^<action android:name="android.service.quicksettings.action.QS_TILE" /^>^</intent-filter^>^</service^>^</application^>^</manifest^> > "%BUILD_DIR%\AndroidManifest.xml"

echo [1/5] Resources created

REM Package resources
"%BUILD_TOOLS%\aapt.exe" package -f -m -J "%BUILD_DIR%\gen" -S "%BUILD_DIR%\res" -M "%BUILD_DIR%\AndroidManifest.xml" -I "%PLATFORM%\android.jar"
if errorlevel 1 goto error
echo [2/5] Resources packaged

REM Compile Java
if not exist "%BUILD_DIR%\src\com\genymobile\scrcpy\companion" mkdir "%BUILD_DIR%\src\com\genymobile\scrcpy\companion"
copy /y "%~dp0app\src\main\java\com\genymobile\scrcpy\companion\*.java" "%BUILD_DIR%\src\com\genymobile\scrcpy\companion\" >nul

javac -encoding UTF-8 -bootclasspath "%PLATFORM%\android.jar" -cp "%BUILD_TOOLS%\core-lambda-stubs.jar" -source 1.8 -target 1.8 -d "%BUILD_DIR%\classes" "%BUILD_DIR%\src\com\genymobile\scrcpy\companion\MainActivity.java" "%BUILD_DIR%\src\com\genymobile\scrcpy\companion\ScrcpyTileService.java" "%BUILD_DIR%\src\com\genymobile\scrcpy\companion\UdpClient.java" "%BUILD_DIR%\gen\com\genymobile\scrcpy\companion\R.java" 2>nul
if errorlevel 1 goto error
echo [3/5] Java compiled

REM Create DEX (use jar to collect all class files, then d8)
pushd "%BUILD_DIR%\classes"
jar cf "%BUILD_DIR%\classes.jar" com
popd
call "%BUILD_TOOLS%\d8.bat" --output "%BUILD_DIR%\dex" --lib "%PLATFORM%\android.jar" --min-api 24 "%BUILD_DIR%\classes.jar" 2>nul
if errorlevel 1 goto error
echo [4/5] DEX created

REM Create APK
"%BUILD_TOOLS%\aapt.exe" package -f -F "%BUILD_DIR%\companion-unsigned.apk" -S "%BUILD_DIR%\res" -M "%BUILD_DIR%\AndroidManifest.xml" -I "%PLATFORM%\android.jar"
pushd "%BUILD_DIR%\dex"
"%BUILD_TOOLS%\aapt.exe" add "%BUILD_DIR%\companion-unsigned.apk" classes.dex >nul
popd

REM Align and sign with v2
"%BUILD_TOOLS%\zipalign.exe" -f 4 "%BUILD_DIR%\companion-unsigned.apk" "%BUILD_DIR%\companion-aligned.apk"
call "%BUILD_TOOLS%\apksigner.bat" sign --ks "%USERPROFILE%\.android\debug.keystore" --ks-pass pass:android --v2-signing-enabled true --v3-signing-enabled true --out "%~dp0scrcpy-companion.apk" "%BUILD_DIR%\companion-aligned.apk"
if errorlevel 1 goto error

echo [5/5] APK created
echo.
echo === BUILD SUCCESSFUL ===
echo Output: %~dp0scrcpy-companion.apk
goto end

:error
echo.
echo === BUILD FAILED ===
exit /b 1

:end
