// Reuse the production QObject adapter and its moc output, without opening a GUI.
#define main backnest_application_main
#include "../desktop/main.cpp"
#undef main
#include <cstdio>

class CheckPage final : public QWebEnginePage {
protected:
    void javaScriptConsoleMessage(JavaScriptConsoleMessageLevel, const QString& message,
                                 int, const QString&) override {
        if (message.startsWith("BACKNEST_CHECK:")) {
            std::fprintf(stdout, "%s\n", message.toUtf8().constData()); std::fflush(stdout);
            QCoreApplication::exit(message == "BACKNEST_CHECK:PASS" ? 0 : 1);
        }
    }
};

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    if (app.arguments().size() != 3) return 2;
    QUrl url(app.arguments()[1]);
    QFile file(app.arguments()[2]);
    if (!file.open(QIODevice::ReadOnly)) return 3;
    const QString html = QString::fromUtf8(file.readAll());
    ApplicationService service(url);
    CheckPage page;
    QWebChannel channel; channel.registerObject("applicationService", &service);
    page.setWebChannel(&channel);
    QTimer::singleShot(60000, &app, [] { QCoreApplication::exit(5); });
    service.initialize([&](bool ready) {
        if (!ready) { app.exit(4); return; }
        page.setHtml(html, url);
    });
    return app.exec();
}
