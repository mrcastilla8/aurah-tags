"""Genera hash de contraseña y secreto de sesión para Render, sin guardar claves en disco."""
import base64
import getpass
import hashlib
import secrets


def b64(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def make_hash(password: str) -> str:
    salt = secrets.token_bytes(24)
    rounds = 390_000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, rounds)
    return f'pbkdf2_sha256${rounds}${b64(salt)}${b64(digest)}'


def main():
    print('AURA Etiquetas · Claves privadas para Render')
    print('La contraseña no se mostrará al escribirla ni se guardará en archivos.')
    password = getpass.getpass('Crea una contraseña (mínimo 14 caracteres): ')
    if len(password) < 14:
        raise SystemExit('La contraseña debe tener al menos 14 caracteres.')
    if getpass.getpass('Repite la contraseña: ') != password:
        raise SystemExit('Las contraseñas no coinciden.')
    print('\nEn Render → servicio → Environment, crea estas DOS variables:')
    print('AURA_PASSWORD_HASH = ' + make_hash(password))
    print('AURA_SESSION_SECRET = ' + secrets.token_urlsafe(48))
    print('\nGuarda tu contraseña en un gestor seguro. No publiques estos valores ni los subas a GitHub.')


if __name__ == '__main__':
    main()
