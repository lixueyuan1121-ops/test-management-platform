const chalk = require('chalk');

class Logger {
  _ts() {
    return new Date().toLocaleTimeString('zh-CN', { hour12: false });
  }

  info(msg) {
    console.log(chalk.blue(`[${this._ts()}]`), msg);
  }

  warn(msg) {
    console.log(chalk.yellow(`[${this._ts()}] ⚠`), msg);
  }

  error(msg) {
    console.log(chalk.red(`[${this._ts()}] ✖`), msg);
  }

  success(msg) {
    console.log(chalk.green(`[${this._ts()}] ✔`), msg);
  }
}

module.exports = Logger;
