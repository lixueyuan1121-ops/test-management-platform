// Machine-wide mutex for desktop automation, including separate runner folders.
// The OS releases the listener on process death: no stale PID files to delete.
const net = require('node:net');
const PORT = 19783;
async function acquireDesktopLease({ port = PORT } = {}) {
  const server = net.createServer(socket => socket.destroy());
  return new Promise((resolve, reject) => {
    server.once('error', error => {
      if (error.code === 'EADDRINUSE') resolve(null);
      else reject(error); // Permission/network errors must not disable isolation.
    });
    server.listen({ host: '127.0.0.1', port, exclusive: true }, () => {
      let closed = false;
      resolve(async () => {
        if (closed) return;
        closed = true;
        await new Promise(done => server.close(done));
      });
    });
  });
}
module.exports = { acquireDesktopLease };
