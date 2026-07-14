#!/usr/bin/env bash

INSTALL_LOG="${SG_AWG_INSTALL_LOG:-/var/log/sg-awg-panel-install.log}"

install_log_init() {
  install -d -m 0755 "$(dirname "$INSTALL_LOG")"
  touch "$INSTALL_LOG"
  chmod 0600 "$INSTALL_LOG"
}

install_info() {
  printf '[SG-AWG-Panel] %s\n' "$*"
}

install_fail() {
  local message="$*"
  printf '[SG-AWG-Panel] ERROR: %s\n' "$message" >&2
  if [[ -s "$INSTALL_LOG" ]]; then
    printf '\nПоследние строки журнала %s:\n' "$INSTALL_LOG" >&2
    tail -n 35 "$INSTALL_LOG" >&2 || true
  fi
  exit 1
}

run_logged() {
  local label="$1"
  shift

  if [[ ! -t 1 ]]; then
    install_info "$label"
    if ! "$@" >>"$INSTALL_LOG" 2>&1; then
      install_fail "$label"
    fi
    return 0
  fi

  local started=$SECONDS
  local pid frame_index=0 elapsed
  local frames='|/-\\'
  local green='' red='' reset=''
  if [[ -z "${NO_COLOR:-}" ]]; then
    green=$'\033[1;32m'
    red=$'\033[1;31m'
    reset=$'\033[0m'
  fi

  "$@" >>"$INSTALL_LOG" 2>&1 &
  pid=$!

  while kill -0 "$pid" 2>/dev/null; do
    elapsed=$((SECONDS - started))
    printf '\r%s[SG-AWG-Panel] [%s]%s %s (%s сек)' \
      "$green" "${frames:frame_index%4:1}" "$reset" "$label" "$elapsed"
    frame_index=$((frame_index + 1))
    sleep 0.25
  done

  if wait "$pid"; then
    elapsed=$((SECONDS - started))
    printf '\r%s[SG-AWG-Panel] [OK]%s %s (%s сек)\033[K\n' \
      "$green" "$reset" "$label" "$elapsed"
  else
    elapsed=$((SECONDS - started))
    printf '\r%s[SG-AWG-Panel] [ОШИБКА]%s %s (%s сек)\033[K\n' \
      "$red" "$reset" "$label" "$elapsed" >&2
    install_fail "$label"
  fi
}

prompt_admin_password() {
  local minimum="${1:-8}"
  local repeated=""

  if [[ -n "${AWGPANEL_ADMIN_PASSWORD:-}" ]]; then
    (( ${#AWGPANEL_ADMIN_PASSWORD} >= minimum )) \
      || install_fail "пароль администратора должен содержать не менее ${minimum} символов"
    export AWGPANEL_ADMIN_PASSWORD
    return 0
  fi

  [[ -r /dev/tty && -w /dev/tty ]] \
    || install_fail "для безопасного ввода пароля требуется обычная SSH-сессия"

  printf '\nSG-AWG-Panel — настройка администратора\n\n' >/dev/tty
  while true; do
    IFS= read -r -s -p "Новый пароль администратора (минимум ${minimum} символов): " \
      AWGPANEL_ADMIN_PASSWORD </dev/tty
    printf '\n' >/dev/tty
    IFS= read -r -s -p "Повторите пароль: " repeated </dev/tty
    printf '\n' >/dev/tty

    if [[ "$AWGPANEL_ADMIN_PASSWORD" != "$repeated" ]]; then
      printf 'Пароли не совпадают. Повторите ввод.\n\n' >/dev/tty
      continue
    fi
    if (( ${#AWGPANEL_ADMIN_PASSWORD} < minimum )); then
      printf 'Пароль должен содержать не менее %s символов.\n\n' "$minimum" >/dev/tty
      continue
    fi
    break
  done

  repeated=""
  export AWGPANEL_ADMIN_PASSWORD
  printf '\n' >/dev/tty
}

prompt_instance_name() {
  local default_name="${1:-SG-AWG-Panel}" value=""

  if [[ -n "${AWGPANEL_INSTANCE_NAME:-}" ]]; then
    (( ${#AWGPANEL_INSTANCE_NAME} <= 64 )) \
      || install_fail "имя сервера должно содержать не более 64 символов"
    export AWGPANEL_INSTANCE_NAME
    return 0
  fi

  [[ -r /dev/tty && -w /dev/tty ]] \
    || install_fail "для ввода имени сервера требуется обычная SSH-сессия"

  while true; do
    IFS= read -r -p "Имя этого сервера [${default_name}]: " value </dev/tty
    value="${value:-$default_name}"
    if [[ -z "$value" || ${#value} -gt 64 || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
      printf 'Имя должно содержать от 1 до 64 обычных символов.\n\n' >/dev/tty
      continue
    fi
    AWGPANEL_INSTANCE_NAME="$value"
    export AWGPANEL_INSTANCE_NAME
    printf '\n' >/dev/tty
    return 0
  done
}

validate_public_port() {
  local value="${1:-}"
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  (( value >= 49152 && value <= 65535 )) || return 1
  [[ "$value" != "585" && "$value" != "18080" ]] || return 1
  return 0
}

public_port_is_free() {
  local value="$1" listeners=""
  if command -v ss >/dev/null 2>&1; then
    listeners="$(ss -H -ltn "sport = :${value}" 2>/dev/null || true)"
    [[ -z "$listeners" ]] || return 1
  fi
  return 0
}

prompt_public_port() {
  local default_port="${1:-62443}" value=""

  if [[ -n "${AWGPANEL_PUBLIC_PORT:-}" ]]; then
    validate_public_port "$AWGPANEL_PUBLIC_PORT" \
      || install_fail "публичный порт панели должен быть в динамическом диапазоне 49152–65535"
    public_port_is_free "$AWGPANEL_PUBLIC_PORT" \
      || install_fail "TCP-порт ${AWGPANEL_PUBLIC_PORT} уже занят"
    export AWGPANEL_PUBLIC_PORT
    return 0
  fi

  [[ -r /dev/tty && -w /dev/tty ]] \
    || install_fail "для выбора публичного порта требуется обычная SSH-сессия"

  while true; do
    IFS= read -r -p "Публичный TCP-порт панели [${default_port}]: " value </dev/tty
    value="${value:-$default_port}"
    if ! validate_public_port "$value"; then
      printf 'Укажите свободный TCP-порт из диапазона 49152–65535.\n\n' >/dev/tty
      continue
    fi
    if ! public_port_is_free "$value"; then
      printf 'TCP-порт %s уже занят. Выберите другой.\n\n' "$value" >/dev/tty
      continue
    fi
    AWGPANEL_PUBLIC_PORT="$value"
    export AWGPANEL_PUBLIC_PORT
    printf '\n' >/dev/tty
    return 0
  done
}

detect_public_ipv4() {
  local value="" token=""

  if command -v curl >/dev/null 2>&1; then
    token="$(curl -fsS --max-time 2 -X PUT \
      -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' \
      http://169.254.169.254/latest/api/token 2>/dev/null || true)"
    if [[ -n "$token" ]]; then
      value="$(curl -fsS --max-time 2 \
        -H "X-aws-ec2-metadata-token: ${token}" \
        http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
    fi
    if [[ -z "$value" ]]; then
      value="$(curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
    fi
  fi

  if [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    printf '%s' "$value"
  fi
}

wait_for_apt() {
  local waited=0
  local timeout="${APT_LOCK_TIMEOUT:-900}"
  local locks=(
    /var/lib/dpkg/lock-frontend
    /var/lib/dpkg/lock
    /var/lib/apt/lists/lock
    /var/cache/apt/archives/lock
  )

  while command -v fuser >/dev/null 2>&1 \
    && fuser "${locks[@]}" >/dev/null 2>&1; do
    if (( waited == 0 )); then
      install_info "Ожидание завершения apt/dpkg..."
    fi
    if (( waited >= timeout )); then
      install_fail "apt/dpkg не освободил блокировку за ${timeout} секунд"
    fi
    sleep 5
    waited=$((waited + 5))
  done
}

require_root() {
  [[ $EUID -eq 0 ]] || install_fail "запустите установщик через sudo"
}

require_supported_architecture() {
  local architecture
  architecture="$(dpkg --print-architecture 2>/dev/null || true)"
  [[ "$architecture" == "amd64" ]] \
    || install_fail "поддерживается только архитектура amd64; обнаружена ${architecture:-unknown}"
}

require_supported_ubuntu() {
  [[ -r /etc/os-release ]] || install_fail "не удалось определить операционную систему"
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || install_fail "поддерживается только Ubuntu"
  case "${VERSION_ID:-}" in
    22.04|24.04) ;;
    *) install_fail "поддерживается Ubuntu 22.04 или 24.04; обнаружена ${VERSION_ID:-unknown}" ;;
  esac
}

require_no_pending_reboot() {
  if [[ -e /var/run/reboot-required ]]; then
    install_fail "после обновления Ubuntu требуется перезагрузка: выполните sudo reboot и запустите установщик снова"
  fi
}
