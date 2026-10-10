# Мастер рядом — приложение для Android (и позже iPhone)

Приложение — нативная оболочка [Capacitor 7](https://capacitorjs.com) вокруг живого сайта
(`server.url` в `capacitor.config.json` → https://master-ryadom.onrender.com). Поэтому:

- любое изменение сайта сразу видно в приложении — переустанавливать APK не нужно;
- пересобирать APK нужно только при изменениях в `mobile/` (иконка, название, адрес сервера, плагины).

Внутри приложения сайт узнаёт его по User-Agent (`MasterRyadomApp`) и показывает нижнюю панель вкладок
(`renderTabbar` в `web/js/nav.js`). Звонки, WhatsApp и внешние ссылки открываются в своих приложениях,
кнопка «Назад» листает историю (плагин `@capacitor/app`), без интернета показывается `www/offline.html`.

## Сборка — на GitHub, локально ничего не нужно

Workflow `.github/workflows/android.yml` собирает APK при каждом push с изменениями в `mobile/`
(или вручную: Actions → android → Run workflow) и публикует GitHub Release. Сайт (`web/app.html`) даёт
постоянную ссылку на последнюю версию:

    https://github.com/TimurBerdyyev/master_ryadom/releases/latest/download/master-ryadom.apk

Номер версии = номер запуска workflow, поэтому новая версия ставится поверх старой.

## Ключ подписи (обязательно сохранить)

Android ставит обновление только если оно подписано **тем же ключом**. Ключ хранится вне репозитория
(у автора — `~/master-ryadom-keys/`, сделайте резервную копию). В GitHub → Settings → Secrets and variables →
Actions → New repository secret добавьте:

| Secret | Значение |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | содержимое `release.p12.base64.txt` |
| `ANDROID_KEYSTORE_PASSWORD` | из `passwords.txt` |
| `ANDROID_KEY_PASSWORD` | из `passwords.txt` |
| `ANDROID_KEY_ALIAS` | `masterryadom` |

Без секретов собирается тестовая (debug) сборка — её можно поставить, но следующая сборка с настоящим
ключом встанет только после удаления старой.

## Локальная сборка (если когда-нибудь понадобится)

Нужны Node 20+, JDK 21 и Android SDK (Android Studio). `npm ci && npx cap sync android`, затем
`cd android && ./gradlew assembleDebug` или откройте `android/` в Android Studio.

## iPhone

Apple не разрешает ставить приложения с сайта — только App Store / TestFlight. Это требует аккаунт
Apple Developer ($99 в год) и сборку в Xcode (на этом Mac macOS 12 — Xcode для публикации не поддерживается;
можно собирать в облаке GitHub Actions на macOS). Пока что на iPhone сайт ставится как приложение:
Safari → «Поделиться» → «На экран „Домой“» (manifest + `web/sw.js`), инструкция — на странице `/app.html`.
Когда будет аккаунт Apple: `npx cap add ios`, иконки, подпись и workflow для TestFlight.
