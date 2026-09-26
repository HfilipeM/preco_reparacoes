#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  Painel de Precos - publicar atualizacoes"
echo "============================================================"
echo
echo "Isto vai enviar os ficheiros data/precos.json e"
echo "data/historico.json para o GitHub, atualizando o site publico."
echo

git add data/precos.json data/historico.json

if git diff --cached --quiet; then
    echo "Nao ha nenhuma alteracao nova para publicar."
    echo "(Ja correste o painel de administracao ou a atualizacao?)"
    exit 0
fi

read -p "Mensagem do commit (Enter para usar 'Atualizacao de precos'): " MSG
MSG=${MSG:-"Atualizacao de precos"}

git commit -m "$MSG"
git push

echo
echo "============================================================"
echo "  Publicado! O site publico deve refletir isto em poucos"
echo "  minutos (o GitHub Pages demora um pouco a atualizar)."
echo "============================================================"
