import json
import socket
import struct
import sys
from queue import Queue

from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


HOST = "127.0.0.1"
PORT = 50505


class NetCom:
    def __init__(self, sock):
        self.sock = sock

    @staticmethod
    def _recv_exact(sock, size):
        chunks = []
        received = 0
        while received < size:
            chunk = sock.recv(size - received)
            if not chunk:
                return None
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

    def recv(self):
        header = self._recv_exact(self.sock, 4)
        if header is None:
            return None

        payload_size = struct.unpack("!I", header)[0]
        payload = self._recv_exact(self.sock, payload_size)
        if payload is None:
            return None

        return json.loads(payload.decode("utf-8"))

    def send(self, message):
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        header = struct.pack("!I", len(payload))
        self.sock.sendall(header + payload)


class ReceiveThread(QThread):
    message_received = pyqtSignal(dict)
    disconnected = pyqtSignal(str) # создаем сигнал для передачи сообщения о потере соединения в MainWindow

    def __init__(self, net):
        super().__init__()
        self.net = net

    def run(self):
        while True:
            try: #перехватываем ошибки, которые могут возникнуть при получении данных
                message = self.net.recv()

            except (OSError, json.JSONDecodeError, UnicodeDecodeError, struct.error) as exc: # обрабатываем сетевые ошибки и ошибки поврежденных данных
                self.disconnected.emit(f"Ошибка соединения: {exc}") # передаем информацию об ошибке в основной поток приложения
                break  # прекращаем получение сообщений после ошибки

            if message is None:
                self.disconnected.emit("Сервер закрыл соединение") # сообщаем основному окну, что сервер отключился
                break

            self.message_received.emit(message)


class SendThread(QThread):
    def __init__(self, net):
        super().__init__()
        self.net = net
        self.queue = Queue()
        self.stopping = False # флаг показывает, была ли запрошена остановка потока

    def send(self, message):
        if self.stopping: # проверяем, не остановлен ли отправляющий поток
            return # не добавляем новые сообщения после команды остановки

        self.queue.put(message)

    def stop(self): # метод запрашивает корректную остановку отправляющего потока
        
        if self.stopping: # проверяем, не отправляли ли уже команду остановки
            return # повторно отправлять команду не требуется

        self.stopping = True # запрещаем добавлять новые сообщения в очередь
        self.queue.put(None) # помещаем специальное значение None для завершения цикла

    def run(self):
        while True:
            message = self.queue.get()
            if message is None:
                break

            try: # обрабатываем ошибку, если соединение уже закрыто
                self.net.send(message) # отправляем сообщение на сервер

            except OSError:  # перехватываем сетевые ошибки при отправке
                self.stopping = True # запрещаем добавлять новые сообщения после ошибки

                break  # завершаем отправляющий поток



class MainWindow(QMainWindow):
    def __init__(self, net):
        super().__init__()
        self.net = net
        self.closing = False # флаг показывает, началось ли закрытие главного окна

        self.setWindowTitle("PyQt6 network chat")
        self.resize(560, 420)

        self.history = QTextEdit()
        self.history.setReadOnly(True)

        self.username_input = QLineEdit() #создаем поле, в котором пользователь сможет указать свое имя
        self.username_input.setPlaceholderText("Enter your name") #добавляем подсказку, которая отображается внутри пустого поля
        self.username_input.setText("User") #устанавливаем имя по умолчанию, если пользователь его не изменит

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a message…")

        self.send_button = QPushButton("Send")
        self.status_label = QLabel("Connected") # создаем индикатор успешного подключения к серверу
        self.status_label.setStyleSheet("color: green;") # устанавливаем зеленый цвет индикатора подключения

        self.clear_button = QPushButton("Clear history") # создаем кнопку для очистки истории сообщений

        input_row = QHBoxLayout()
        input_row.addWidget(self.input)
        input_row.addWidget(self.send_button)
        input_row.addWidget(self.clear_button)  # добавляем кнопку очистки истории рядомс кнопкой Send

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.username_input) #добавляем поле ввода имени пользователя над историей сообщений
        layout.addWidget(self.history)
        layout.addLayout(input_row)

        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        self.send_button.clicked.connect(self.on_send_clicked)
        self.input.returnPressed.connect(self.on_send_clicked)
        self.clear_button.clicked.connect(self.on_clear_clicked) #при нажатии Clear history вызываем метод очистки истории сообщений

        self.receive_thread = ReceiveThread(self.net)
        self.receive_thread.message_received.connect(self.handle_message)
        self.receive_thread.disconnected.connect(self.on_disconnected) # подключаем сигнал потери соединения к обработчику главного окна
        self.receive_thread.start()

        self.send_thread = SendThread(self.net)
        self.send_thread.start()

    def on_send_clicked(self):
        text = self.input.text().strip()
        if not text:
            return
        
        username = self.username_input.text().strip() #получаем имя пользователя и удаляем лишние пробелы по краям

        if not username: #проверяем, указал ли пользователь свое имя
            username = "User" #если имя не указано, используем стандартное имя User

        self.send_thread.send({
            "type": "chat",
            "username": username, #передаем имя пользователя, который отправил сообщение
            "text": text,
        })

        self.input.clear()

    def on_clear_clicked(self): # метод вызывается при нажатии кнопки Clear history
        self.history.clear() # очищаем историю сообщений только в текущем окне приложения

    def handle_message(self, message):
        message_type = message.get("type") #получаем тип входящего сообщения из словаря

        if message_type == "chat": #проверяем, является ли сообщение обычным сообщением пользователя
            username = message.get("username", "User") #получаем имя отправителя; если его нет, используем User
            text = message.get("text", "") #получаем текст сообщения;если его нет, используем пустую строку

            self.history.append(f"{username}: {text}") #добавляем в историю имя отправителя и текст сообщения
        
        elif message_type == "system": #проверяем, является ли сообщение системным
            text = message.get("text", "") #получаем текст системного сообщения

            self.history.append(f"[Система] {text}") #отображаем системное сообщение с пометкой [Система]
        
        # если мы не знаем тип сообщения программа просто ничего не делает
        # это позволяет избежать ошибок при получении неподдерживаемых типов

    def on_disconnected(self, reason):  # ьетод вызывается при обнаружении потери соединения с сервером

        if self.closing: # проверяем, не закрывает ли пользователь приложение самостоятельно
            return  # не показываем сообщение о потере соединения при обычном выходе

        self.status_label.setText("Disconnected")  # меняем надпись о состоянии подключения
        self.status_label.setStyleSheet("color: red;") # изменяем цвет надписи на красный
        self.history.append(f"[Система] Соединение потеряно. {reason}") # добавляем в историю системное сообщение о потере соединения
        self.send_button.setEnabled(False) # запрещаем отправку новых сообщений после отключения
        self.input.setEnabled(False) # блокируем поле ввода, поскольку сервер больше не принимает сообщения

    
    def closeEvent(self, event): # метод вызывается при закрытии окна приложения
        self.closing = True # отмечаем, что отключение сети происходит при закрытии приложения
        self.send_thread.stop() # отправляем команду остановки через очередь SendThread

        try: # пытаемся закрыть сетевое соединение в обе стороны
            self.net.sock.shutdown(socket.SHUT_RDWR)  # shutdown разблокирует поток, ожидающий данные в recv()

        except OSError: # игнорируем ошибку, если соединение уже было закрыто
            pass

        self.net.sock.close() # закрываем сокет после остановки сетевого обмена
        send_finished = self.send_thread.wait(2000) # ожидаем завершения отправляющего потока не более 2 секунд
        receive_finished = self.receive_thread.wait(2000) # ожидаем завершения принимающего потока не более 2 секунд

        if send_finished and receive_finished: # проверяем, что оба потока действительно завершились
            event.accept() # разрешаем Qt закрыть окно приложения

        else:
            event.ignore()  # не закрываем окно, пока остался работающий поток
            print("Не удалось завершить все потоки. Повторите закрытие окна.") # выводим предупреждение в терминал для диагностики


def main():
    app = QApplication(sys.argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((HOST, PORT))
    except OSError as exc:
        print(f"Could not connect to server at {HOST}:{PORT}: {exc}")
        print("Run server.py first, then start main.py.")
        return

    net = NetCom(sock)
    window = MainWindow(net)
    window.show()

    exit_code = app.exec()

    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
