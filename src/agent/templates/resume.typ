// One-page, single-column, ATS-friendly resume. Data arrives as JSON in sys.inputs.data.
#let d = json(bytes(sys.inputs.data))
#let b = d.basics
#set document(title: b.name + " – Resume", author: b.name)
#set page(paper: "a4", margin: (x: 1.6cm, y: 1.4cm))
#set text(font: "New Computer Modern", size: 10pt, lang: "en")
#set par(justify: false, leading: 0.55em)

#let section(title) = {
  v(0.6em)
  text(size: 11pt, weight: "bold", upper(title))
  v(-0.6em)
  line(length: 100%, stroke: 0.5pt)
  v(-0.2em)
}

#align(center)[
  #text(size: 17pt, weight: "bold", b.name) \
  #text(size: 10.5pt, b.headline) \
  #b.location · #b.email · #b.phone · #b.linkedin
]

#section("Summary")
#d.summary

#section("Skills")
#d.skills.join(" · ")

#section("Experience")
#for e in d.experience [
  #grid(columns: (1fr, auto), [*#e.title*, #e.company], [#e.dates])
  #if e.projects.len() > 0 [#text(style: "italic", "Projects: " + e.projects.join(" · ")) \ ]
  #for bullet in e.bullets [- #bullet
  ]
  #v(0.3em)
]

#if d.certifications.len() > 0 [
  #section("Certifications")
  #d.certifications.join(" · ")
]

#section("Education")
#d.education.join(" · ")
