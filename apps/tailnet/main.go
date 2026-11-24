// Command openprogram-tailnet puts this computer's OpenProgram on the
// internet through Tailscale Funnel, with Tailscale embedded (tsnet), so the
// owner installs nothing: they sign in to Tailscale once in a browser, and any
// device then reaches https://<name>.<tailnet>.ts.net. Reaching the address
// is not signing in: every device still pairs with a one-time code.
//
// The Worker runs it as a child (openprogram/host/security/remote_access.py)
// and reads one JSON object per line on stdout:
//
//	{"state":"starting"}
//	{"state":"needs_login","login_url":"https://login.tailscale.com/a/..."}
//	{"state":"needs_funnel","enable_url":"https://login.tailscale.com/f/funnel?...","message":"..."}
//	{"state":"running","url":"https://<name>.<tailnet>.ts.net"}
//	{"state":"error","message":"..."}
//
// It stops on SIGTERM, SIGINT or when stdin closes.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"

	"tailscale.com/client/local"
	"tailscale.com/envknob"
	"tailscale.com/ipn"
	"tailscale.com/tsnet"
)

// The port Funnel answers on; the address it gives is plain https://<name>.
const funnelPort = 443

var (
	emitMu     sync.Mutex
	lastLine   string
	linkInText = regexp.MustCompile(`https://\S+[^.\s]`)
)

// emit writes one state line, skipping a repeat of the previous one.
func emit(fields map[string]string) {
	data, err := json.Marshal(fields)
	if err != nil {
		return
	}
	emitMu.Lock()
	defer emitMu.Unlock()
	if string(data) == lastLine {
		return
	}
	lastLine = string(data)
	os.Stdout.Write(append(data, '\n'))
}

func fail(message string) {
	emit(map[string]string{"state": "error", "message": message})
}

// loopbackTarget accepts only http://<loopback>:<port>: the proxy never
// forwards the internet to anything but this computer's own Worker.
func loopbackTarget(raw string) (*url.URL, error) {
	target, err := url.Parse(raw)
	if err != nil || target.Scheme != "http" || target.Port() == "" {
		return nil, fmt.Errorf("--target must be http://127.0.0.1:<port>, got %q", raw)
	}
	host := target.Hostname()
	if ip := net.ParseIP(host); host != "localhost" && (ip == nil || !ip.IsLoopback()) {
		return nil, fmt.Errorf("--target must be a loopback address, got %q", raw)
	}
	return target, nil
}

// proxy passes every request, websockets included, to the Worker. The Host
// stays the public name so the Worker checks the Origin the browser dialled,
// and X-Forwarded-Proto says it arrived over https; anything the client sent
// as X-Forwarded-* is dropped first.
func proxy(target *url.URL) http.Handler {
	return &httputil.ReverseProxy{
		Rewrite: func(r *httputil.ProxyRequest) {
			r.SetURL(target)
			r.Out.Host = r.In.Host
			r.SetXForwarded()
		},
		FlushInterval: -1,
		ErrorLog:      log.New(io.Discard, "", 0),
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, _ error) {
			w.WriteHeader(http.StatusBadGateway)
		},
	}
}

type sidecar struct {
	srv     *tsnet.Server
	lc      *local.Client
	handler http.Handler

	browseURL string // the last login link the backend asked to open
	enableURL string // the admin link that turns on HTTPS and Funnel
	serving   *http.Server
	address   string
}

// step looks at the backend once and moves toward serving.
func (s *sidecar) step(ctx context.Context) {
	st, err := s.lc.StatusWithoutPeers(ctx)
	if err != nil {
		if ctx.Err() == nil {
			fail(err.Error())
		}
		return
	}
	switch st.BackendState {
	case ipn.NeedsLogin.String():
		link := st.AuthURL
		if link == "" {
			link = s.browseURL
		}
		if link == "" {
			emit(map[string]string{"state": "starting"})
			return
		}
		emit(map[string]string{"state": "needs_login", "login_url": link})
	case ipn.NeedsMachineAuth.String():
		emit(map[string]string{"state": "needs_login", "login_url": "https://login.tailscale.com/admin/machines",
			"message": "An admin of this tailnet has to approve this computer."})
	case ipn.Running.String():
		if s.serving != nil {
			emit(map[string]string{"state": "running", "url": s.address})
			return
		}
		if err := ipn.CheckFunnelAccess(funnelPort, st.Self); err != nil || len(st.CertDomains) == 0 {
			s.needsFunnel(ctx, err)
			return
		}
		s.serve(st.CertDomains[0])
	default:
		emit(map[string]string{"state": "starting"})
	}
}

// needsFunnel asks the coordination server for the link that enables HTTPS
// certificates and Funnel for this node, the one `tailscale funnel` prints.
func (s *sidecar) needsFunnel(ctx context.Context, cause error) {
	message := "HTTPS and Funnel are not enabled for this tailnet."
	if cause != nil {
		message = cause.Error()
	}
	if s.enableURL == "" {
		if answer, err := s.lc.QueryFeature(ctx, "funnel"); err == nil && answer != nil {
			s.enableURL = answer.URL
			if answer.Text != "" {
				message = strings.TrimSpace(answer.Text)
			}
		}
	}
	link := s.enableURL
	if link == "" {
		link = linkInText.FindString(message)
	}
	emit(map[string]string{"state": "needs_funnel", "enable_url": link, "message": message})
}

func (s *sidecar) serve(domain string) {
	listener, err := s.srv.ListenFunnel("tcp", fmt.Sprintf(":%d", funnelPort))
	if err != nil {
		fail(err.Error())
		return
	}
	s.address = "https://" + strings.TrimSuffix(domain, ".")
	s.serving = &http.Server{Handler: s.handler, ReadHeaderTimeout: 30 * time.Second,
		ErrorLog: log.New(io.Discard, "", 0)}
	go func(server *http.Server) {
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			fail(err.Error())
		}
	}(s.serving)
	emit(map[string]string{"state": "running", "url": s.address})
}

func run(ctx context.Context, stateDir, hostname string, target *url.URL, verbose bool) error {
	// No log uploads to Tailscale; the owner's traffic is theirs.
	envknob.SetNoLogsNoSupport()
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return err
	}
	quiet := func(string, ...any) {}
	logf := quiet
	if verbose {
		logf = log.New(os.Stderr, "tsnet: ", log.LstdFlags).Printf
	} else {
		// Some Tailscale packages write to the standard logger directly.
		log.SetOutput(io.Discard)
	}
	srv := &tsnet.Server{Dir: stateDir, Hostname: hostname, Logf: logf, UserLogf: quiet}
	defer srv.Close()
	if err := srv.Start(); err != nil {
		return err
	}
	lc, err := srv.LocalClient()
	if err != nil {
		return err
	}
	s := &sidecar{srv: srv, lc: lc, handler: proxy(target)}
	defer func() {
		if s.serving != nil {
			s.serving.Close()
		}
	}()

	notes := make(chan *ipn.Notify, 8)
	go func() {
		watcher, err := lc.WatchIPNBus(ctx, ipn.NotifyInitialState)
		if err != nil {
			return
		}
		defer watcher.Close()
		for {
			note, err := watcher.Next()
			if err != nil {
				return
			}
			select {
			case notes <- &note:
			case <-ctx.Done():
				return
			}
		}
	}()

	tick := time.NewTicker(3 * time.Second)
	defer tick.Stop()
	for {
		s.step(ctx)
		select {
		case <-ctx.Done():
			return nil
		case note := <-notes:
			if note.BrowseToURL != nil && *note.BrowseToURL != "" {
				s.browseURL = *note.BrowseToURL
			}
			if note.ErrMessage != nil {
				fail(*note.ErrMessage)
			}
		case <-tick.C:
		}
	}
}

func main() {
	stateDir := flag.String("state-dir", "", "where this node keeps its Tailscale identity")
	hostname := flag.String("hostname", "openprogram", "the name this computer asks for on the tailnet")
	targetFlag := flag.String("target", "", "the Worker, http://127.0.0.1:<port>")
	verbose := flag.Bool("verbose", false, "write Tailscale's own log to stderr")
	flag.Parse()

	emit(map[string]string{"state": "starting"})
	if *stateDir == "" {
		fail("--state-dir is required")
		os.Exit(2)
	}
	target, err := loopbackTarget(*targetFlag)
	if err != nil {
		fail(err.Error())
		os.Exit(2)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer cancel()
	// The Worker holds stdin open; when it goes away, so does this.
	go func() {
		io.Copy(io.Discard, os.Stdin)
		cancel()
	}()

	if err := run(ctx, *stateDir, *hostname, target, *verbose); err != nil {
		fail(err.Error())
		os.Exit(1)
	}
}
