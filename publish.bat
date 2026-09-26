@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Painel de Precos - publicar atualizacoes
echo ============================================================
echo.
echo Isto vai enviar os ficheiros data\precos.json e
echo data\historico.json para o GitHub, atualizando o site publico.
echo.

git add data\precos.json data\historico.json

git diff --cached --quiet
if %errorlevel%==0 (
    echo Nao ha nenhuma alteracao nova para publicar.
    echo ^(Ja correste o painel de administracao ou a atualizacao?^)
    pause
    exit /b 0
)

set /p DATA=Data/hora para a mensagem do commit (ou deixa em branco): 
if "%DATA%"=="" set DATA=Atualizacao de precos

git commit -m "%DATA%"
git push

echo.
echo ============================================================
echo   Publicado! O site publico deve refletir isto em poucos
echo   minutos (o GitHub Pages demora um pouco a atualizar).
echo ============================================================
pause
