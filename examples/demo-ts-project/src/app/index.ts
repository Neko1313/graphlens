import { EventEmitter } from "events";
import _ from "lodash";
import { UserService } from "./service";
import { greet } from "./utils";

export class App extends EventEmitter {
  private service: UserService;

  constructor() {
    super();
    this.service = new UserService();
  }

  run(): void {
    const users = this.service.findAll();
    const names = _.map(users, (u) => greet(u.name));
    this.emit("ready", names);
  }
}
