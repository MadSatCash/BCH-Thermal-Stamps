@echo off
cd /d "%~dp0"
echo Empaquetando BCH Thermal Stamps en un unico .exe...

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "BCH-Thermal-Stamps" ^
  --collect-all bitcash ^
  --collect-all coincurve ^
  --collect-all qrcode ^
  run.py

echo.
echo Listo. El ejecutable quedo en:  dist\BCH-Thermal-Stamps.exe
echo Copia ese .exe a donde quieras. No necesita instalar nada.
pause
