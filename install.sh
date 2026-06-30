#!/usr/bin/env bash
set -Eeuo pipefail

RELEASE_VERSION="v0.1.0-rc4"
REPOSITORY="s-gor/sg-awg-panel"
ARCHIVE_URL="https://github.com/${REPOSITORY}/archive/refs/tags/${RELEASE_VERSION}.tar.gz"
INSTALL_LOG="/var/log/sg-awg-panel-install.log"
TMP_DIR=""
ADMIN_PASSWORD=""
PASSWORD_MIN_LENGTH=8
PUBLIC_PORT_DEFAULT=62443

info() {
  printf '[SG-AWG-Panel] %s\n' "$*"
}

fail() {
  printf '[SG-AWG-Panel] ERROR: %s\n' "$*" >&2
  if [[ -s "$INSTALL_LOG" ]]; then
    printf '\nПоследние строки журнала %s:\n' "$INSTALL_LOG" >&2
    tail -n 35 "$INSTALL_LOG" >&2 || true
  fi
  exit 1
}


run_step() {
  local label="$1"
  shift

  if [[ ! -t 1 ]]; then
    info "$label"
    "$@" >>"$INSTALL_LOG" 2>&1 || fail "$label"
    return 0
  fi

  local started=$SECONDS pid frame_index=0 elapsed
  local frames='|/-\\'
  "$@" >>"$INSTALL_LOG" 2>&1 &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    elapsed=$((SECONDS - started))
    printf '\r[SG-AWG-Panel] [%s] %s (%s сек)' \
      "${frames:frame_index%4:1}" "$label" "$elapsed"
    frame_index=$((frame_index + 1))
    sleep 0.25
  done
  if wait "$pid"; then
    elapsed=$((SECONDS - started))
    printf '\r[SG-AWG-Panel] [OK] %s (%s сек)\033[K\n' "$label" "$elapsed"
  else
    elapsed=$((SECONDS - started))
    printf '\r[SG-AWG-Panel] [ОШИБКА] %s (%s сек)\033[K\n' "$label" "$elapsed" >&2
    fail "$label"
  fi
}

cleanup() {
  unset AWGPANEL_ADMIN_PASSWORD ADMIN_PASSWORD ADMIN_PASSWORD_REPEAT || true
  if [[ -n "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
  return 0
}
trap cleanup EXIT

[[ $EUID -eq 0 ]] || fail "запустите ссылку через sudo bash"
[[ -r /etc/os-release ]] || fail "не удалось определить операционную систему"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || fail "поддерживается только Ubuntu"
case "${VERSION_ID:-}" in
  22.04|24.04) ;;
  *) fail "поддерживается Ubuntu 22.04 или 24.04; обнаружена ${VERSION_ID:-unknown}" ;;
esac
ARCHITECTURE="$(dpkg --print-architecture 2>/dev/null || true)"
[[ "$ARCHITECTURE" == "amd64" ]] \
  || fail "поддерживается только архитектура amd64; обнаружена ${ARCHITECTURE:-unknown}"

bootstrap_tools() {
  apt-get -o Dpkg::Use-Pty=0 update -qq
  env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a \
    apt-get -o Dpkg::Use-Pty=0 install -y -qq curl ca-certificates tar
}

if [[ -e /var/run/reboot-required ]]; then
  fail "после обновления Ubuntu требуется перезагрузка: выполните sudo reboot и запустите установщик снова"
fi

if [[ -e /opt/sg-awg-panel || -e /etc/sg-awg-panel || -e /var/lib/sg-awg-panel ]]; then
  fail "обнаружена существующая установка. Этот установщик предназначен для нового чистого EC2"
fi

[[ -r /dev/tty && -w /dev/tty ]] || fail "для безопасного ввода пароля требуется обычная SSH-сессия"

printf '\nSG-AWG-Panel %s — чистая установка\n\n' "$RELEASE_VERSION" >/dev/tty
while true; do
  IFS= read -r -s -p "Новый пароль администратора (минимум ${PASSWORD_MIN_LENGTH} символов): " ADMIN_PASSWORD </dev/tty
  printf '\n' >/dev/tty
  IFS= read -r -s -p "Повторите пароль: " ADMIN_PASSWORD_REPEAT </dev/tty
  printf '\n' >/dev/tty

  if [[ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD_REPEAT" ]]; then
    printf 'Пароли не совпадают. Повторите ввод.\n\n' >/dev/tty
    continue
  fi
  if (( ${#ADMIN_PASSWORD} < PASSWORD_MIN_LENGTH )); then
    printf 'Пароль должен содержать не менее %s символов.\n\n' "$PASSWORD_MIN_LENGTH" >/dev/tty
    continue
  fi
  break
done
unset ADMIN_PASSWORD_REPEAT
export AWGPANEL_ADMIN_PASSWORD="$ADMIN_PASSWORD"

while true; do
  IFS= read -r -p "Публичный TCP-порт панели [${PUBLIC_PORT_DEFAULT}]: " AWGPANEL_PUBLIC_PORT </dev/tty
  AWGPANEL_PUBLIC_PORT="${AWGPANEL_PUBLIC_PORT:-$PUBLIC_PORT_DEFAULT}"
  if [[ ! "$AWGPANEL_PUBLIC_PORT" =~ ^[0-9]+$ ]] \
     || (( AWGPANEL_PUBLIC_PORT < 49152 || AWGPANEL_PUBLIC_PORT > 65535 )); then
    printf 'Укажите TCP-порт из динамического диапазона 49152–65535.\n\n' >/dev/tty
    continue
  fi
  if command -v ss >/dev/null 2>&1 \
     && [[ -n "$(ss -H -ltn "sport = :${AWGPANEL_PUBLIC_PORT}" 2>/dev/null || true)" ]]; then
    printf 'TCP-порт %s уже занят. Выберите другой.\n\n' "$AWGPANEL_PUBLIC_PORT" >/dev/tty
    continue
  fi
  break
done
printf '\n' >/dev/tty
export AWGPANEL_PUBLIC_PORT

install -d -m 0755 "$(dirname "$INSTALL_LOG")"
: >"$INSTALL_LOG"
chmod 0600 "$INSTALL_LOG"

if ! command -v curl >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then
  run_step "Установка curl и tar..." bootstrap_tools
fi

TMP_DIR="$(mktemp -d /tmp/sg-awg-panel-install.XXXXXX)"
run_step "Загрузка ${RELEASE_VERSION} из GitHub..." \
  curl -fsSL --retry 3 --connect-timeout 15 "$ARCHIVE_URL" -o "$TMP_DIR/source.tar.gz"
run_step "Распаковка исходников..." \
  tar -xzf "$TMP_DIR/source.tar.gz" -C "$TMP_DIR"
SOURCE_DIR="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d -name 'sg-awg-panel-*' | head -n 1)"
[[ -n "$SOURCE_DIR" && -f "$SOURCE_DIR/deploy/first-install.sh" ]] \
  || fail "архив GitHub не содержит полный установщик"

SOURCE_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/v\1/p' "$SOURCE_DIR/awgpanel/__init__.py" | head -n 1)"
[[ "$SOURCE_VERSION" == "$RELEASE_VERSION" ]] \
  || fail "версия архива ${SOURCE_VERSION:-unknown} не совпадает с ${RELEASE_VERSION}"

if ! bash "$SOURCE_DIR/deploy/first-install.sh"; then
  fail "установка не завершена"
fi

INSTALLED_VERSION="$(/opt/sg-awg-panel/.venv/bin/python -c 'import awgpanel; print("v" + awgpanel.__version__)' 2>/dev/null || true)"
[[ "$INSTALLED_VERSION" == "$RELEASE_VERSION" ]] \
  || fail "после установки обнаружена версия ${INSTALLED_VERSION:-unknown}, ожидалась ${RELEASE_VERSION}"

unset AWGPANEL_ADMIN_PASSWORD ADMIN_PASSWORD
printf '\n'
info "Установка завершена: ${INSTALLED_VERSION}"
info "Журнал: ${INSTALL_LOG}"
