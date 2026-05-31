#include <iostream>

// urblock_po: интеграция биометрии с входом в Linux (PAM).
// См. docs/LOGIN.ru.md и scripts/urblock-verify

int main() {
    std::cout << "Urblock PO — вход в систему по лицу\n"
              << "  Регистрация:  ./scripts/urblock-enroll\n"
              << "  Проверка:     sudo PAM_USER=$USER ./scripts/urblock-verify\n"
              << "  Установка:    sudo ./scripts/install-login.sh\n";
    return 0;
}
