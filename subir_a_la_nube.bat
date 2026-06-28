@echo off
echo ==============================================
echo  SUBIENDO ACTUALIZACIONES DEL BOT A RENDER
echo ==============================================
echo.

git add .
git commit -m "Actualizacion automatica del bot"

echo.
echo ==============================================
echo  1. SI TE APARECE UNA VENTANA DE GITHUB:
echo     Dale clic a "Sign in with your browser"
echo ==============================================
echo.

git push origin main

echo.
echo ==============================================
echo  ¡LISTO!
echo  Si no hubo errores, Render ya esta 
echo  actualizando tu bot en la nube automaticamente.
echo ==============================================
pause
