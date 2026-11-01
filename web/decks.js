// The cards of each deck, read by the bot (deck.py) and by the fairness checker
// (fair.js), so a game is always replayed with the deck it was dealt from.
// "copies" is how many of each value every color gets; "specials" are the
// colorless cards and how many of each. Everything after "var DECKS =" must
// stay plain JSON (no comments, no trailing commas): the bot parses it as such.
var DECKS = {
 "nitro": {
  "colors": ["r", "b", "g", "y"],
  "values": [
   {"values": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "draw", "reverse", "skip"], "copies": 2}
  ],
  "specials": {"nitro": 8}
 },
 "classic": {
  "colors": ["r", "b", "g", "y"],
  "values": [
   {"values": ["0"], "copies": 1},
   {"values": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "draw", "reverse", "skip"], "copies": 2}
  ],
  "specials": {"colorchooser": 4, "draw_four": 4}
 },
 "rainbow": {
  "colors": ["r", "b", "g", "y", "p", "o"],
  "values": [
   {"values": ["1", "2", "3", "4", "5", "6"], "copies": 2},
   {"values": ["draw", "reverse", "skip"], "copies": 2}
  ],
  "specials": {"colorchooser": 4, "draw_four": 4}
 },
 "wild": {
  "colors": ["r", "b", "g", "y"],
  "values": [
   {"values": ["1", "2", "3", "4", "5", "draw", "reverse", "skip"], "copies": 4}
  ],
  "specials": {"colorchooser": 6, "draw_four": 6}
 },
 "storm": {
  "colors": ["r", "b", "g", "y"],
  "values": [
   {"values": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "draw", "reverse", "skip", "swap", "drop", "plus1"], "copies": 2}
  ],
  "specials": {"colorchooser": 4, "draw_four": 4}
 },
 "crazy": {
  "colors": ["r", "b", "g", "y"],
  "values": [
   {"values": ["0"], "copies": 1},
   {"values": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "draw", "reverse", "skip"], "copies": 2}
  ],
  "specials": {"colorchooser": 4, "draw_four": 4, "draw_eight": 2}
 },
 "diablo": {
  "colors": ["r", "b", "g", "y"],
  "values": [
   {"values": ["0"], "copies": 1},
   {"values": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "draw", "reverse", "skip"], "copies": 2}
  ],
  "specials": {"colorchooser": 4, "draw_four": 4, "diablo": 8}
 },
 "xeno": {
  "colors": ["r", "b", "g", "y"],
  "values": [
   {"values": ["0"], "copies": 1},
   {"values": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "draw", "alpha"], "copies": 2}
  ],
  "specials": {"xeno": 8}
 }
};
