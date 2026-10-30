# Marca MesaCerta

Símbolo **"M que confere"**: as hastes do M em grafite, com o meio do M formando um ✓ verde. O ícone de app é sempre branco.

## Cores

| Uso | Sobre fundo claro | Sobre fundo escuro |
|---|---|---|
| Hastes do M e nome | `#1F2523` (grafite) | `#F2F4F3` |
| ✓ | `#22A55E` (verde conferido) | `#4CD08A` |
| Fundo do ícone | `#FFFFFF` | `#FFFFFF` |

O verde é usado **só no ✓**.

## Tipografia

Plus Jakarta Sans (Google Fonts, licença OFL): "Mesa" em peso 500, "Certa" em peso 800, espaçamento entre letras de -0,02em. Nos SVGs da logo o texto já está convertido em contorno, então não é preciso ter a fonte instalada.

## Arquivos

| Arquivo | Para quê |
|---|---|
| `svg/mesacerta-simbolo.svg` / `-escuro.svg` | Só o símbolo, fundo transparente (caixa de 64×64) |
| `svg/mesacerta-logo.svg` / `-escuro.svg` | Símbolo + nome, horizontal |
| `svg/mesacerta-icone.svg` | Ícone de app quadrado (fonte para gerar outros tamanhos) |
| `svg/mesacerta-icone-arredondado.svg` | Ícone com cantos arredondados, para site, apresentações e redes |
| `ios/AppIcon-1024.png` | Ícone do iOS / App Store: 1024×1024, sem transparência e sem cantos (o iOS aplica a máscara) |
| `android/drawable/ic_launcher_foreground.xml` | Camada da frente do ícone adaptativo (vetor) |
| `android/drawable/ic_launcher_monochrome.xml` | Camada para ícones temáticos (Android 13+) |
| `android/mipmap-anydpi-v26/ic_launcher*.xml` | Ícone adaptativo com fundo branco |
| `android/mipmap-*/ic_launcher.png` | Ícones legados (48 a 192 px) para Android 7 ou anterior |
| `android/play-store-512.png` | Ícone da Google Play: 512×512, sem transparência |
| `png/` | Logo e símbolo em PNG para usos rápidos (documentos, redes sociais) |

As pastas `android/` seguem a estrutura de `app/src/main/res/` e podem ser copiadas direto para lá.

## Regras de uso

- Deixe ao redor do símbolo um respiro de pelo menos metade da largura do M.
- Não mude as cores, não aplique sombra ou gradiente e não distorça.
- Em fundo escuro, use as versões `-escuro`.
- Tamanho mínimo do símbolo: 16 px.
