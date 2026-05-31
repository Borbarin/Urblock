cmake_minimum_required(VERSION 3.16)
project(urblock_po LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)

# Демо-бинарник CLion (не участвует во входе в систему)
add_executable(urblock_po main.cpp)

install(PROGRAMS
    scripts/urblock-verify
    scripts/urblock-enroll
    DESTINATION bin
)

install(FILES
    docs/LOGIN.ru.md
    DESTINATION share/doc/urblock_po
)
