#include <QApplication>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMessageBox>
#include <QNetworkAccessManager>
#include <QNetworkProxy>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QTimer>
#include <QUrlQuery>
#include <QWebChannel>
#include <QWebEnginePage>
#include <QWebEngineScript>
#include <QWebEngineScriptCollection>
#include <QWebEngineView>
#include <functional>

// C++ application-service adapter. Registry comes from the running backend,
// so Qt and HTTP use the same operation IDs, schemas, errors and job lifecycle.
class ApplicationService final : public QObject {
    Q_OBJECT
    QNetworkAccessManager network;
    QUrl base;
    QByteArray token;
    QHash<QString, QJsonObject> operations;
public:
    explicit ApplicationService(QUrl url, QObject* parent = nullptr) : QObject(parent), base(url) {
        network.setProxy(QNetworkProxy::NoProxy);
    }
    void initialize(std::function<void(bool)> finished) {
        auto* reply = network.get(QNetworkRequest(base.resolved(QUrl("/api/session"))));
        connect(reply, &QNetworkReply::finished, this, [this, reply, finished] {
            token = QJsonDocument::fromJson(reply->readAll()).object()["token"].toString().toUtf8();
            reply->deleteLater();
            if (token.isEmpty()) { finished(false); return; }
            QNetworkRequest request(base.resolved(QUrl("/api/v1/capabilities")));
            request.setRawHeader("X-BackNest-Token", token);
            auto* catalog = network.get(request);
            connect(catalog, &QNetworkReply::finished, this, [this, catalog, finished] {
                const auto list = QJsonDocument::fromJson(catalog->readAll()).object()["operations"].toArray();
                for (const auto& value : list) { const auto op = value.toObject(); operations.insert(op["id"].toString(), op); }
                catalog->deleteLater(); finished(!operations.empty());
            });
        });
    }
public slots:
    void invoke(const QString& id, const QString& operationId, const QVariantMap& arguments) {
        if (!operations.contains(operationId)) { emit completed(id, 404, QVariantMap{{"error", "Unknown operation"}}); return; }
        const auto operation = operations.value(operationId);
        QString path = operation["path"].toString();
        const auto params = arguments.value("path").toMap();
        for (auto it = params.begin(); it != params.end(); ++it)
            path.replace("{" + it.key() + "}", QString::fromLatin1(QUrl::toPercentEncoding(it.value().toString())));
        if (path.contains('{')) { emit completed(id, 400, QVariantMap{{"error", "Missing path parameter"}}); return; }
        QUrl url = base.resolved(QUrl(path)); QUrlQuery query;
        const auto values = arguments.value("query").toMap();
        for (auto it = values.begin(); it != values.end(); ++it) query.addQueryItem(it.key(), it.value().toString());
        url.setQuery(query);
        QNetworkRequest request(url);
        request.setTransferTimeout(125000);
        request.setRawHeader("X-BackNest-Token", token);
        request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
        const QByteArray body = arguments.contains("body") ? QJsonDocument(QJsonObject::fromVariantMap(arguments.value("body").toMap())).toJson(QJsonDocument::Compact) : QByteArray();
        auto* reply = network.sendCustomRequest(request, operation["method"].toString().toLatin1(), body);
        connect(reply, &QNetworkReply::finished, this, [this, reply, id] {
            auto data = QJsonDocument::fromJson(reply->readAll());
            const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
            // API consumers receive arrays as well as objects via QVariant.
            emit completed(id, status ? status : 503, data.isNull() ? QVariant(QVariantMap{{"error", reply->errorString()}}) : data.toVariant());
            reply->deleteLater();
        });
    }
signals:
    void completed(const QString& id, int status, const QVariant& payload);
};

class LocalPage final : public QWebEnginePage {
    QUrl origin;
public:
    LocalPage(const QUrl& url, QObject* parent) : QWebEnginePage(parent), origin(url) {}
protected:
    bool acceptNavigationRequest(const QUrl& url, NavigationType, bool) override {
        return url.scheme() == origin.scheme() && url.host() == origin.host() && url.port() == origin.port();
    }
};

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    const auto args = app.arguments();
    const int option = args.indexOf("--url");
    const QUrl url(option >= 0 && option + 1 < args.size() ? args[option + 1] : QString());
    if (url.scheme() != "http" || url.host() != "127.0.0.1" || url.port() <= 0) return 2;
    QWebEngineView view; view.setWindowTitle(QString::fromUtf8("归档 BackNest")); view.resize(1280, 850);
    auto* page = new LocalPage(url, &view); view.setPage(page);
    ApplicationService service(url);
    QWebChannel channel; channel.registerObject("applicationService", &service); page->setWebChannel(&channel);
    QFile source(":/qtwebchannel/qwebchannel.js");
    if (!source.open(QIODevice::ReadOnly)) return 3;
    QWebEngineScript script; script.setName("BackNest WebChannel"); script.setSourceCode(QString::fromUtf8(source.readAll()));
    script.setInjectionPoint(QWebEngineScript::DocumentCreation); script.setWorldId(QWebEngineScript::MainWorld);
    page->scripts().insert(script);
    service.initialize([&](bool ready) {
        if (!ready) { QMessageBox::critical(nullptr, "BackNest", "Cannot connect to local application service."); app.exit(4); return; }
        view.load(url); view.show();
    });
    return app.exec();
}
#include "main.moc"
