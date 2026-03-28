import _ from "lodash";
import { greet } from "./utils";

export interface User {
  id: number;
  name: string;
  email: string;
}

export abstract class BaseRepository<T> {
  protected items: T[] = [];

  abstract findById(id: number): T | undefined;

  findAll(): T[] {
    return [...this.items];
  }
}

export class UserService extends BaseRepository<User> {
  constructor() {
    super();
    this.items = [
      { id: 1, name: "Alice", email: "alice@example.com" },
      { id: 2, name: "Bob", email: "bob@example.com" },
    ];
  }

  findById(id: number): User | undefined {
    return _.find(this.items, (u) => u.id === id);
  }

  greetUser(id: number): string {
    const user = this.findById(id);
    return user ? greet(user.name) : "Unknown user";
  }
}
