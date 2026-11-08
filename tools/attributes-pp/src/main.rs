use std::error::Error;
use std::io::{self, BufRead, ErrorKind, Write};

use rosu_pp::any::HitResultPriority;
use rosu_pp::osu::OsuDifficultyAttributes;
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
struct Input {
    attributes: Attributes,
    metadata: Metadata,
}

#[derive(Debug, Deserialize)]
struct Attributes {
    star_rating: f64,
    max_combo: u32,
    aim_difficulty: f64,
    aim_difficult_slider_count: f64,
    speed_difficulty: f64,
    speed_note_count: f64,
    #[serde(default)]
    flashlight_difficulty: f64,
    slider_factor: f64,
    #[serde(default)]
    aim_top_weighted_slider_factor: f64,
    #[serde(default)]
    speed_top_weighted_slider_factor: f64,
    aim_difficult_strain_count: f64,
    speed_difficult_strain_count: f64,
    #[serde(default)]
    nested_score_per_object: f64,
    #[serde(default)]
    legacy_score_base_multiplier: f64,
    #[serde(default)]
    maximum_legacy_combo_score: f64,
}

#[derive(Debug, Deserialize)]
struct Metadata {
    ar: Option<f64>,
    od: Option<f64>,
    hp: Option<f64>,
    count_circles: Option<u32>,
    count_sliders: Option<u32>,
    count_spinners: Option<u32>,
}

#[derive(Debug, Serialize)]
struct Output {
    pp_nm_100_fc: f64,
    pp_nm_98_fc: f64,
    pp_nm_95_fc: f64,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
enum Response {
    Estimate(Output),
    Error(ErrorOutput),
}

#[derive(Debug, Serialize)]
struct ErrorOutput {
    error: String,
}

fn hit_window(difficulty: f64, min: f64, mid: f64, max: f64) -> f64 {
    let value = if difficulty > 5.0 {
        mid + (max - mid) * ((difficulty - 5.0) / 5.0)
    } else if difficulty < 5.0 {
        mid + (mid - min) * ((difficulty - 5.0) / 5.0)
    } else {
        mid
    };

    value.floor() - 0.5
}

fn required<T>(value: Option<T>, name: &'static str) -> Result<T, io::Error> {
    value.ok_or_else(|| io::Error::new(ErrorKind::InvalidData, format!("missing {name}")))
}

fn build_attributes(input: &Input) -> Result<OsuDifficultyAttributes, io::Error> {
    let metadata = &input.metadata;
    let ar = required(metadata.ar, "ar")?;
    let od = required(metadata.od, "od")?;
    let hp = required(metadata.hp, "hp")?;
    let circles = required(metadata.count_circles, "count_circles")?;
    let sliders = required(metadata.count_sliders, "count_sliders")?;
    let spinners = required(metadata.count_spinners, "count_spinners")?;

    Ok(OsuDifficultyAttributes {
        aim: input.attributes.aim_difficulty,
        aim_difficult_slider_count: input.attributes.aim_difficult_slider_count,
        speed: input.attributes.speed_difficulty,
        flashlight: input.attributes.flashlight_difficulty,
        slider_factor: input.attributes.slider_factor,
        aim_top_weighted_slider_factor: input.attributes.aim_top_weighted_slider_factor,
        speed_top_weighted_slider_factor: input.attributes.speed_top_weighted_slider_factor,
        speed_note_count: input.attributes.speed_note_count,
        aim_difficult_strain_count: input.attributes.aim_difficult_strain_count,
        speed_difficult_strain_count: input.attributes.speed_difficult_strain_count,
        nested_score_per_object: input.attributes.nested_score_per_object,
        legacy_score_base_multiplier: input.attributes.legacy_score_base_multiplier,
        maximum_legacy_combo_score: input.attributes.maximum_legacy_combo_score,
        ar,
        great_hit_window: hit_window(od, 80.0, 50.0, 20.0),
        ok_hit_window: hit_window(od, 140.0, 100.0, 60.0),
        meh_hit_window: hit_window(od, 200.0, 150.0, 100.0),
        hp,
        n_circles: circles,
        n_sliders: sliders,
        n_large_ticks: 0,
        n_spinners: spinners,
        stars: input.attributes.star_rating,
        max_combo: input.attributes.max_combo,
    })
}

fn hitresults_for_accuracy(attributes: &OsuDifficultyAttributes, accuracy: f64) -> (u32, u32) {
    let total_hits = attributes.n_objects();
    let n100 = f64::round((1.0 - accuracy / 100.0) * 1.5 * f64::from(total_hits)) as u32;
    let n100 = n100.min(total_hits);

    (total_hits - n100, n100)
}

fn calculate(attributes: OsuDifficultyAttributes, accuracy: f64) -> Result<f64, Box<dyn Error>> {
    let max_combo = attributes.max_combo;
    let (n300, n100) = hitresults_for_accuracy(&attributes, accuracy);
    let total_hits = f64::from(n300 + n100);
    let realized_accuracy = if total_hits == 0.0 {
        accuracy
    } else {
        (f64::from(6 * n300 + 2 * n100) / (6.0 * total_hits)) * 100.0
    };
    let result = attributes
        .performance()
        .lazer(false)
        .hitresult_priority(HitResultPriority::BestCase)
        .accuracy(realized_accuracy)
        .combo(max_combo)
        .n300(n300)
        .n100(n100)
        .n50(0)
        .misses(0)
        .calculate()?;
    let pp = result.pp();
    if !pp.is_finite() {
        return Err(io::Error::new(ErrorKind::InvalidData, "non-finite PP result").into());
    }
    Ok(pp)
}

fn estimate(input: Input) -> Result<Output, Box<dyn Error>> {
    let attributes = build_attributes(&input)?;
    Ok(Output {
        pp_nm_100_fc: calculate(attributes.clone(), 100.0)?,
        pp_nm_98_fc: calculate(attributes.clone(), 98.0)?,
        pp_nm_95_fc: calculate(attributes, 95.0)?,
    })
}

fn main() -> Result<(), Box<dyn Error>> {
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout().lock());

    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }

        let response = match serde_json::from_str::<Input>(&line) {
            Ok(input) => match estimate(input) {
                Ok(output) => Response::Estimate(output),
                Err(error) => Response::Error(ErrorOutput {
                    error: error.to_string(),
                }),
            },
            Err(error) => Response::Error(ErrorOutput {
                error: error.to_string(),
            }),
        };
        serde_json::to_writer(&mut stdout, &response)?;
        stdout.write_all(b"\n")?;
        stdout.flush()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Input {
        Input {
            attributes: Attributes {
                star_rating: 5.0,
                max_combo: 1000,
                aim_difficulty: 2.5,
                aim_difficult_slider_count: 20.0,
                speed_difficulty: 2.5,
                speed_note_count: 200.0,
                flashlight_difficulty: 0.0,
                slider_factor: 0.8,
                aim_top_weighted_slider_factor: 0.5,
                speed_top_weighted_slider_factor: 0.5,
                aim_difficult_strain_count: 50.0,
                speed_difficult_strain_count: 50.0,
                nested_score_per_object: 1.0,
                legacy_score_base_multiplier: 1.0,
                maximum_legacy_combo_score: 500_000.0,
            },
            metadata: Metadata {
                ar: Some(9.0),
                od: Some(8.0),
                hp: Some(5.0),
                count_circles: Some(1000),
                count_sliders: Some(0),
                count_spinners: Some(0),
            },
        }
    }

    #[test]
    fn fc_estimates_decrease_with_accuracy() {
        let output = estimate(sample()).expect("valid fixture should calculate");

        assert!(output.pp_nm_100_fc > output.pp_nm_98_fc);
        assert!(output.pp_nm_98_fc > output.pp_nm_95_fc);
    }

    #[test]
    fn accuracy_loss_uses_hundreds_without_misses() {
        let input = sample();
        let attributes = build_attributes(&input).expect("valid fixture should build");
        assert_eq!(hitresults_for_accuracy(&attributes, 98.0), (970, 30));
        assert_eq!(hitresults_for_accuracy(&attributes, 95.0), (925, 75));
    }

    #[test]
    fn missing_metadata_is_reported_instead_of_defaulted() {
        let mut input = sample();
        input.metadata.count_sliders = None;

        assert!(build_attributes(&input).is_err());
    }
}
